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
    monkeypatch.setattr(C.trace_log, "LOGS", tmp_path / "logs")
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


# ------------------------------------------------------------------ away from the examples


def test_sample_mode_refuses_a_point_outside_the_extracts_with_the_areas_it_covers(sample):
    for path, body in [("/api/diagnose", {"lat": 51.75, "lon": -1.25}), ("/api/evidence", {"lat": 51.75, "lon": -1.25})]:
        r = sample.post(path, json=body)
        assert r.status_code == 404 and "Sample mode only has data around" in r.json()["detail"]
        assert "great langdale" in r.json()["detail"]


def test_a_point_inside_the_extracts_with_no_recording_runs_rules_only(sample):
    r = sample.post("/api/diagnose", json={"lat": 52.31, "lon": -3.52})  # 1.4 km from Near Rhayader's recorded point
    assert r.status_code == 200 and r.json()["sample_mode"] == "rules only"
    run = wait(sample, r.json()["run_id"])
    res = run["result"]
    assert run["status"] == "complete" and res["rules_only"] is True and not res.get("replayed")
    assert res["hypotheses"] and all(h["supporting"] for h in res["hypotheses"])
    ids = {e["evidence_id"] for e in run["evidence"]}
    assert {"speed_cell", "nearest_site", "los_ee"} <= ids  # the real tools ran on the extracts
    calls = [c for rd in run["trace"]["rounds"] for c in rd["llm_calls"]]
    assert calls and {c["provider"] for c in calls} == {"rules"}
    assert any("coverage checker" in a["text"] for a in res["actions"])
    letter = sample.post(f"/api/runs/{res['run_id']}/letter", json={"recipient": "operator"})
    assert letter.status_code == 200 and "rules only" in letter.json()["body_markdown"]


@needs_examples
def test_a_recorded_example_replays_at_its_recorded_point_only(sample):
    ex = EXAMPLES["near_rhayader"]
    r = sample.post("/api/diagnose", json={"lat": ex["lat"] + 0.0005, "lon": ex["lon"]})  # ~56 m away
    assert r.json()["sample_mode"] == "recorded" and r.json()["location"] == {"lat": ex["lat"], "lon": ex["lon"]}
    assert 40 < r.json()["moved_to_recorded_point_m"] < 70
    assert wait(sample, r.json()["run_id"])["result"]["replayed"] is True
    r = sample.post("/api/diagnose", json={"lat": ex["lat"] - 0.004, "lon": ex["lon"] + 0.013})  # ~1 km: not replayed
    assert r.json()["sample_mode"] == "rules only"
    assert wait(sample, r.json()["run_id"])["result"]["rules_only"] is True


def test_a_saved_postcode_far_from_the_point_is_not_reported(sample):
    from coverage_detective.sources import geocode
    assert geocode.reverse(54.47, -3.07).data["found"] is False  # Great Langdale's saved postcode is 3 km away
    near = geocode.reverse(54.445, -3.105).data
    assert near["found"] and near["distance_m"] < 2000


@needs_examples
def test_each_run_writes_a_jsonl_agent_trace_with_handoffs_queries_and_models(sample, tmp_path, capsys):
    ex = EXAMPLES["near_rhayader"]
    run = wait(sample, sample.post("/api/diagnose", json={"lat": ex["lat"], "lon": ex["lon"]}).json()["run_id"])
    path = tmp_path / "logs" / f"{run['run_id']}.jsonl"
    rows = [json.loads(x) for x in path.read_text().splitlines()]
    assert all(set(r) == set(C.trace_log.FIELDS) for r in rows) and all(r["timestamp"] for r in rows)
    actions = [r["action"] for r in rows]
    assert {"investigator", "diagnostician", "critic", "advisor", "guard"} <= {r["agent_name"] for r in rows}
    assert any(r["action"] == "critique_needs_evidence" and r["target_agent"] == "investigator" for r in rows)
    assert actions.count("round_start") == 2 and actions[-1] == "run_end"
    assert any(a.startswith("zetaris_query") for a in actions) and "hmem_read" in actions and "world_write_relationship" in actions
    calls = [r for r in rows if r["action"].startswith("model_call")]
    assert calls and all(r["provider"] == "recorded" and "nemotron" in r["model_answered"] for r in calls)
    assert path.read_text() in capsys.readouterr().out  # also on stdout


# ------------------------------------------------------------------ POST /run


@needs_examples
def test_run_waits_and_returns_the_diagnosis_as_json(sample):
    ex = EXAMPLES["great_langdale"]
    r = sample.post("/run", json={"lat": ex["lat"], "lon": ex["lon"]})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["status"] == "complete" and out["mode"] == "recorded replies (sample mode)"
    assert out["headline"] and out["causes"][0]["evidence_ids"] and out["trace"] == f"logs/{out['run_id']}.jsonl"
    assert any(m["agent"] == "critic" and "nemotron" in m["model_answered"] for m in out["models"])
    zone = sample.post("/run", json={"zone": "Z00072"}).json()
    assert zone["status"] == "complete" and zone["fix_plan"] and zone["headline"] != out["headline"]


def test_run_rules_only_refusals_and_bad_input_are_clear_json(sample):
    rules = sample.post("/run", json={"lat": 52.31, "lon": -3.52}).json()
    assert rules["mode"] == "rules only, no model (sample mode)" and rules["causes"]
    far = sample.post("/run", json={"lat": 51.75, "lon": -1.25})
    assert far.status_code == 404 and far.json()["status"] == "error" and "Sample mode only has data" in far.json()["error"]
    assert sample.post("/run", json={"zone": "Z99999"}).json()["status"] == "error"
    bad = sample.post("/run", json={"lat": 54.4, "place": "LA22 9JY"})
    assert bad.status_code == 422 and "exactly one of" in bad.text
    assert sample.post("/run", json={"lat": 10, "lon": 0}).status_code == 422  # outside the UK


def test_run_answers_still_running_instead_of_hanging(sample, monkeypatch):
    monkeypatch.setattr(API, "_start", lambda run_id, fn, **kw: None)  # a run that never finishes
    r = sample.post("/run", json={"lat": 52.31, "lon": -3.52, "timeout_s": 5})
    assert r.status_code == 202 and r.json()["status"] == "running" and r.json()["poll"].startswith("/api/runs/")
