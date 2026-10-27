"""Agent API endpoints, offline: runs are started with a fake coordinator; no LLM, Zetaris or Meterless."""

import json
import time

import pytest
from fastapi.testclient import TestClient

from coverage_detective.agents import coordinator as C
from coverage_detective.api import agents as API
from coverage_detective.api.app import app


class FakeMemory:
    mode = "service"

    def __init__(self):
        self.n = 0

    def create_run(self, scope, **kw):
        self.n += 1
        return f"run_test_{self.n}"

    def audit(self, run_id):
        return []

    notes = critiques = beliefs = evidence = audit

    def world_for_run(self, run_id):
        return {"entities": [], "relationships": []}


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(C, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(API, "memory", FakeMemory())
    monkeypatch.setattr(API.llm, "_key", "test-key")
    monkeypatch.setattr(API.llm, "model", "nvidia/m")
    monkeypatch.setattr(API.ev, "resolve", lambda q, lat, lon: (
        {"kind": "point", "lat": 54.445, "lon": -3.105, "nearest_postcode": "LA22 9JY"}, None))
    return TestClient(app), tmp_path


def fake_run(run_id, **kw):
    """What the coordinator leaves behind: the progress file and the export."""
    steps = [{"n": 1, "agent": "investigator", "round": 1, "kind": "llm", "label": "investigator", "status": "done", "duration_ms": 900},
             {"n": 2, "agent": "investigator", "round": 1, "kind": "tool", "label": "speeds_near()", "status": "done"},
             {"n": 3, "agent": "advisor", "round": 0, "kind": "llm", "label": "advisor", "status": "done"}]
    result = {"status": "complete", "headline": "Hills likely block your signal [los_ee].", "hypotheses": [], "rounds": 1,
              "scope": "point", "location": {"lat": 54.445, "lon": -3.105}}
    (C.RUNS_DIR / f"{run_id}.json").write_text(json.dumps({"run_id": run_id, "scope": "point", "status": "complete",
                                                           "steps": steps, "result": result, "elapsed_s": 1.0}))
    notes = [{"type": "tool_call", "round": 1, "tool": "speeds_near", "args": {}, "evidence_ids": ["speed_cell"]},
             {"type": "llm_call", "round": 1, "by": "investigator", "model": "nvidia/x", "duration_ms": 900},
             {"type": "guard", "round": 1, "agent": "critic", "guard_kind": "weak_cap", "summary": "capped"},
             {"type": "llm_call", "round": 0, "by": "advisor", "model": "nvidia/x", "duration_ms": 500}]
    full = {"audit": [{"seq": 1, "action": "create", "kind": "run"}], "notes": notes,
            "critiques": [{"round": 1, "belief_id": "terrain_blocking", "verdict": "approve", "reason": "ok"}],
            "beliefs": [{"belief_id": "terrain_blocking", "history": [{"round": 1, "by": "diagnostician", "confidence": 0.7}]}],
            "evidence": [{"evidence_id": "speed_cell", "round": 1, "finding": "x", "zetaris": [{"id": "Q1", "sql": "SELECT 1"}]}],
            "world": {"entities": [], "relationships": []}, "memory_mode": "service"}
    (C.RUNS_DIR / f"{run_id}.full.json").write_text(json.dumps(full))


def wait_done(c, run_id):
    for _ in range(50):
        r = c.get(f"/api/runs/{run_id}").json()
        if r["status"] not in ("queued", "running"):
            return r
        time.sleep(0.02)
    raise AssertionError("run did not finish")


def test_diagnose_starts_a_run_and_polling_returns_progress_trace_and_evidence(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(C, "diagnose_point", fake_run)
    r = c.post("/api/diagnose", json={"lat": 54.445, "lon": -3.105})
    assert r.status_code == 200 and r.json()["status"] == "queued"
    run = wait_done(c, r.json()["run_id"])
    assert run["status"] == "complete" and len(run["steps"]) == 3
    rounds = run["trace"]["rounds"]
    assert [x["round"] for x in rounds] == [1, 0]  # the final card comes last
    assert rounds[0]["tools"][0]["evidence_ids"] == ["speed_cell"] and rounds[0]["guard"][0]["kind"] == "weak_cap"
    assert rounds[0]["revisions"][0]["to"] == 0.7 and rounds[0]["critiques"][0]["verdict"] == "approve"
    assert run["evidence"][0]["zetaris"][0]["sql"] == "SELECT 1"


def test_running_run_has_no_detail_unless_asked(client, monkeypatch):
    c, runs = client
    (runs / "run_x.json").write_text(json.dumps({"run_id": "run_x", "status": "running", "steps": [], "result": None}))
    monkeypatch.setattr(API, "run_detail", lambda run_id: {"trace": {"rounds": []}, "evidence": [], "world": {}})
    assert "trace" not in c.get("/api/runs/run_x").json()
    assert "trace" in c.get("/api/runs/run_x?detail=1").json()


def test_unknown_run_is_404_and_letter_needs_a_finished_run(client):
    c, runs = client
    assert c.get("/api/runs/nope").status_code == 404
    (runs / "run_y.json").write_text(json.dumps({"run_id": "run_y", "status": "running", "steps": [], "result": None}))
    assert c.post("/api/runs/run_y/letter", json={"recipient": "operator"}).status_code == 409
    assert c.post("/api/runs/run_y/letter", json={"recipient": "king"}).status_code == 422


def test_diagnose_needs_a_location_and_an_llm_key(client, monkeypatch):
    c, _ = client
    assert c.post("/api/diagnose", json={}).status_code == 422
    monkeypatch.setattr(API.llm, "_key", None)
    monkeypatch.delenv("METERLESS_RUNTIME_TOKEN", raising=False)
    monkeypatch.delenv("METERLESS_RUNTIME_TOKEN_FILE", raising=False)
    r = c.post("/api/diagnose", json={"query": "LA22 9JY"})
    assert r.status_code == 503 and "METERLESS_RUNTIME_TOKEN" in r.json()["detail"]


def test_a_crashing_run_is_marked_failed(client, monkeypatch):
    c, _ = client

    def boom(run_id, **kw):
        raise RuntimeError("kaput")

    monkeypatch.setattr(C, "diagnose_point", boom)
    run_id = c.post("/api/diagnose", json={"query": "LA22 9JY"}).json()["run_id"]
    run = wait_done(c, run_id)
    assert run["status"] == "failed" and "kaput" in run["result"]["notes"][0]
