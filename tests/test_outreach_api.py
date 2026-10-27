"""The outreach endpoints: recipients for a district, and the email for a finished run (offline)."""

import json

import pytest
from fastapi.testclient import TestClient

from coverage_detective.agents import coordinator as C
from coverage_detective.agents.schemas import Letter
from coverage_detective.api import agents as API
from coverage_detective.api.app import app
from coverage_detective.config import ROOT

RESULT = json.loads((ROOT / "examples" / "great_langdale" / "diagnosis.json").read_text())
LETTER = {
    "subject": "Poor mobile signal at Great Langdale (LA22 9JY)",
    "body_markdown": ("# Mobile signal in Great Langdale\n\n"
                      "I live at [Your address] and the signal is poor [los_ee].\n\n"
                      "---\n**Evidence and sources**\n\n"
                      "- [los_ee] EE's nearest approximate site is 3.8 km away (Copernicus DEM; as of 2026-10-25)\n"),
}
DRAFTED = ("# Mobile signal in Great Langdale\n\nI live at [Your address] and the signal is poor [los_ee].\n\n"
           "---\n**Evidence and sources**\n\n- [los_ee] EE's site is 3.8 km away\n")


class FakeMemory:
    mode = "service"

    def __init__(self):
        self.recorded = [{"type": "guard", "agent": "letter_writer", "summary": "an earlier draft's correction"}]

    def notes(self, run_id):
        return list(self.recorded)

    def add_note(self, run_id, kind, summary, **kw):
        self.recorded.append({"type": kind, "agent": kw.get("by"), "summary": summary})


@pytest.fixture
def client(monkeypatch, tmp_path):
    (tmp_path / "run_1.json").write_text(json.dumps({"run_id": "run_1", "status": "complete", "steps": [],
                                                     "result": RESULT}))
    (tmp_path / "run_running.json").write_text(json.dumps({"run_id": "run_running", "status": "running",
                                                           "steps": [], "result": None}))
    monkeypatch.setattr(C, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(API, "memory", FakeMemory())
    monkeypatch.setattr(API.llm, "_key", None)
    monkeypatch.setattr(API.llm, "model", "m")
    return TestClient(app), tmp_path


def test_recipients_endpoint_lists_the_council_and_networks_with_their_addresses(client):
    c, _ = client
    body = c.get("/api/outreach/recipients", params={"district": "Westmorland and Furness"}).json()
    assert body["council_known"] is True
    assert [(r["name"], r["email"]) for r in body["recipients"]][0] == (
        "Westmorland and Furness Council", "complaints@westmorlandandfurness.gov.uk")
    assert [r["name"] for r in body["recipients"]][1:] == ["EE", "O2", "Three", "Vodafone"]


def test_recipients_endpoint_says_when_it_has_no_council_contact(client):
    c, _ = client
    body = c.get("/api/outreach/recipients", params={"district": "Gwynedd"}).json()
    assert body["council_known"] is False
    assert body["recipients"][0]["routes"][0]["value"] == "https://www.gov.uk/find-local-council"


def test_email_endpoint_composes_for_the_runs_council_from_the_supplied_letter(client):
    c, _ = client
    r = c.post("/api/runs/run_1/email", json={"to": "council", "sent_by": "D. Abraham",
                                              "subject": LETTER["subject"],
                                              "body_markdown": LETTER["body_markdown"]})
    assert r.status_code == 200
    body = r.json()
    assert body["to"] == "complaints@westmorlandandfurness.gov.uk"
    assert body["district"] == "Westmorland and Furness"
    assert "complaints@westmorlandandfurness.gov.uk" in body["mailto"]
    assert body["contact"]["name"] == "Westmorland and Furness Council"
    assert body["guard"] == []                                   # no model ran, so no corrections to claim


def test_a_supplied_letter_has_its_bracketed_ids_removed_before_it_is_sent(client):
    c, _ = client
    body = c.post("/api/runs/run_1/email", json={"to": "council", "subject": "Signal",
                                                 "body_markdown": "Poor here [los_ee] and [made_up]."}).json()
    assert "[los_ee]" not in body["body_text"] and "[made_up]" not in body["body_text"]
    assert "Poor here and" in body["body_text"]


def test_email_endpoint_reports_only_the_guard_notes_its_own_draft_earned(client, monkeypatch):
    c, _ = client

    def fake_letter(case, recipient, diagnosis, addressee=None):
        case.memory.add_note(case.run_id, "guard", "removed [invented] from this draft", by="letter_writer")
        return Letter(recipient=recipient, subject="Signal at LA22 9JY", body_markdown=DRAFTED,
                      cited_evidence=["los_ee"])

    monkeypatch.setattr(API.A, "write_letter", fake_letter)
    monkeypatch.setattr(API.llm, "_key", "test-key")
    monkeypatch.setattr(API.llm, "model", "m")
    body = c.post("/api/runs/run_1/email", json={"to": "EE"}).json()
    assert body["guard"] == ["removed [invented] from this draft"]
    assert body["to"] == "customer.complaints@ee.co.uk"


def test_email_endpoint_needs_a_finished_run_and_a_known_recipient(client):
    c, _ = client
    assert c.post("/api/runs/nope/email", json={"to": "council"}).status_code == 404
    assert c.post("/api/runs/run_running/email", json={"to": "council"}).status_code == 409
    assert c.post("/api/runs/run_1/email", json={"to": "BT"}).status_code == 422
    assert c.post("/api/runs/run_1/email", json={"to": "council"}).status_code == 503  # no key, no supplied text
