"""Sample mode end to end: DuckDB over the committed extracts, saved live responses and recorded agent replies.
No Zetaris, no network, no LLM key (any LLM call fails the test); Meterless is the in-memory fallback."""

import functools
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from coverage_detective.agents import coordinator as C
from coverage_detective.agents import llm as L
from coverage_detective.agents import replay as R
from coverage_detective.api import agents as API
from coverage_detective.memory.meterless import Meterless

INDEX = json.loads(R.INDEX.read_text()) if R.INDEX.exists() else {"examples": []}
EXAMPLES = {e["example_id"]: e for e in INDEX["examples"]}
needs_examples = pytest.mark.skipif(not EXAMPLES, reason="examples not recorded (scripts/record_examples.py)")


def test_prompts_are_frozen_with_the_recorded_examples():
    assert EXAMPLES, "examples/index.json is missing"
    assert R.prompts_sha() == INDEX["prompts_sha"], "a prompt changed after the examples were recorded: re-record them"


def test_replay_keys_and_missing_replies_fail_clearly():
    s = R.Session.__new__(R.Session)
    s.mode, s.example_id, s.counts, s.variant = "replay", "x", {}, None
    s.data = {"replies": {"critic|round 1|call 1": {"parsed": None, "tool_calls": [], "content": "ok"}}}
    assert [s.next_key("critic", 1), s.next_key("critic", 1), s.next_key("critic", 2)] == \
        ["critic|round 1|call 1", "critic|round 1|call 2", "critic|round 2|call 1"]
    assert s.reply("critic|round 1|call 1", None).model == "recorded"
    with pytest.raises(R.ReplayMissing, match="no recorded reply for this step"):
        s.reply("critic|round 1|call 2", None)
    with pytest.raises(R.ReplayMissing, match="no recorded run for this point"):
        R.find("point", lat=50.0, lon=-5.0)


@pytest.fixture
def sample(monkeypatch, tmp_path):
    monkeypatch.setenv("SAMPLE_MODE", "1")
    monkeypatch.delenv("RECORD_EXAMPLES", raising=False)
    monkeypatch.setattr(C, "RUNS_DIR", tmp_path)
    from coverage_detective.data import terrain
    monkeypatch.setattr(terrain, "_dem", None)  # the committed DEM crops, not data/raw/dem
    monkeypatch.setattr(terrain, "DEM_DIR", tmp_path / "no_raw_dem")

    def no_llm(*a, **k):
        raise AssertionError("sample mode must not call the LLM")

    monkeypatch.setattr(L.LLM, "_post", no_llm)
    monkeypatch.setattr(httpx, "request", lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectError("offline")))
    monkeypatch.setattr("coverage_detective.memory.meterless.time.sleep", lambda s: None)
    mem = Meterless("http://127.0.0.1:1")  # in-memory fallback
    monkeypatch.setattr(API, "memory", mem)
    monkeypatch.setattr(C, "diagnose_point", functools.partial(C.diagnose_point, memory=mem))
    monkeypatch.setattr(C, "diagnose_zone", functools.partial(C.diagnose_zone, memory=mem))
    from coverage_detective.api.app import app
    return TestClient(app)


def wait(c, run_id, limit=120):
    for _ in range(limit * 10):
        run = c.get(f"/api/runs/{run_id}").json()
        if run["status"] not in ("queued", "running"):
            return run
        time.sleep(0.1)
    raise AssertionError("run did not finish")


@needs_examples
@pytest.mark.parametrize("example_id", [e for e, x in EXAMPLES.items() if x["kind"] == "point"])
def test_point_examples_replay_through_the_api(sample, example_id):
    ex = EXAMPLES[example_id]
    r = sample.post("/api/diagnose", json={"lat": ex["lat"], "lon": ex["lon"]})
    assert r.status_code == 200, r.text
    run = wait(sample, r.json()["run_id"])
    res = run["result"]
    assert run["status"] == ex["status"] == "complete" and res["replayed"] and res["example_id"] == example_id
    assert res["headline"] == ex["headline"]  # the same evidence and replies give the same answer
    assert run["trace"]["rounds"] and run["evidence"]
    assert res["wording_check"]["ok"], res["wording_check"]


@needs_examples
def test_zone_and_opportunity_replay(sample):
    for example_id in ("zone_ousby", "opportunity_windermere"):
        ex = EXAMPLES[example_id]
        r = sample.post(f"/api/council/zones/{ex['zone_id']}/diagnose")
        run = wait(sample, r.json()["run_id"])
        assert run["result"]["headline"] == ex["headline"] and run["result"]["replayed"]
    assert run["result"]["zone_type"] == "opportunity" and run["result"]["wording_check"]["ok"]


@needs_examples
def test_letters_summary_and_unrecorded_requests(sample):
    ex = EXAMPLES["great_langdale"]
    run = wait(sample, sample.post("/api/diagnose", json={"lat": ex["lat"], "lon": ex["lon"]}).json()["run_id"])
    for recipient in ex["letters"]:
        letter = sample.post(f"/api/runs/{run['run_id']}/letter", json={"recipient": recipient}).json()
        assert letter["recipient"] == recipient and "Evidence and sources" in letter["body_markdown"]
    for to, address in (("council", "complaints@westmorlandandfurness.gov.uk"),
                        ("EE", "customer.complaints@ee.co.uk")):
        mail = sample.post(f"/api/runs/{run['run_id']}/email", json={"to": to, "sent_by": "D. Abraham"})
        assert mail.status_code == 200, mail.text
        body = mail.json()
        assert body["to"] == address and body["mailto"].startswith(f"mailto:{address}")
        assert "Reported by D. Abraham" in body["body_text"] and "Diagnosis reference" in body["body_text"]
    form_only = sample.post(f"/api/runs/{run['run_id']}/email", json={"to": "Three"}).json()
    assert form_only["to"] == "" and form_only["channel"] == "form" and form_only["routes"][0]["kind"] == "form"
    summary = sample.post("/api/council/summary", json={"scope": "uk"})
    assert summary.status_code == 200 and summary.json()["sections"]
    missing = sample.post("/api/diagnose", json={"lat": 51.0, "lon": -2.0})
    assert missing.status_code == 404 and "no recorded run" in missing.json()["detail"]


@needs_examples
def test_insufficient_data_example_says_so():
    d = json.loads((R.EXAMPLES / "remote_powys" / "diagnosis.json").read_text())
    text = " ".join([d["headline"], d.get("explanation") or "", *d.get("limitations", []),
                     *(a["text"] for a in d.get("actions", []))]).lower()
    assert "operators could not be identified" in text and "no coverage" not in text.replace("not the same as no coverage", "")
