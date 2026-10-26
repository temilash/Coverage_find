"""Agent endpoints: start a run in the background, poll it, draft letters and emails, summaries.

    POST /api/diagnose {query | lat, lon, audience}        -> {run_id}
    POST /api/council/zones/{zone_id}/diagnose             -> {run_id}
    GET  /api/runs/{run_id}                                -> status, progress steps, result, trace, evidence, world
    POST /api/runs/{run_id}/letter {recipient}             -> evidence letter (Markdown)
    GET  /api/outreach/recipients?district=                -> the council and the four networks, with their routes
    POST /api/runs/{run_id}/email {to, sent_by}            -> the email for that recipient (subject, text, html, mailto)
    POST /api/council/summary {scope, weights}             -> priority summary for the scope (Markdown)

Runs take minutes, so they run in a thread (at most MAX_RUNNING at once; later ones wait as
"queued") and the page polls GET /api/runs/{id}: the progress file has one step per agent call
and tool call. A finished run is read from its export (data/runs/<id>.full.json).
"""

from __future__ import annotations

import json
import threading
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, model_validator

from coverage_detective import evidence as ev
from coverage_detective import outreach as O
from coverage_detective.agents import agents as A
from coverage_detective.agents import coordinator as C
from coverage_detective.agents.llm import LLMError, llm
from coverage_detective.agents.tools import Case
from coverage_detective.config import UK_BBOX
from coverage_detective.memory.meterless import memory

router = APIRouter(prefix="/api", tags=["agents"])
MAX_RUNNING = 2
_slots = threading.BoundedSemaphore(MAX_RUNNING)
_threads: dict[str, threading.Thread] = {}


class DiagnoseRequest(BaseModel):
    query: str | None = Field(None, min_length=2, max_length=100, description="UK postcode or place name")
    lat: float | None = Field(None, ge=UK_BBOX["min_lat"], le=UK_BBOX["max_lat"])
    lon: float | None = Field(None, ge=UK_BBOX["min_lon"], le=UK_BBOX["max_lon"])
    audience: Literal["resident", "council officer", "operator"] = "resident"

    @model_validator(mode="after")
    def one_location(self):
        if not self.query and (self.lat is None or self.lon is None):
            raise ValueError('give "query", or both "lat" and "lon"')
        return self


class LetterRequest(BaseModel):
    recipient: Literal["operator", "council", "mp"] = "operator"


class EmailRequest(BaseModel):
    """to: "council", or the network name. body_markdown: reuse a drafted letter and skip the model."""

    to: str = Field("council", min_length=2, max_length=80, description='"council", EE, O2, Three or Vodafone')
    sent_by: str = Field("", max_length=120, description="the resident's name for the signature (optional)")
    subject: str | None = Field(None, max_length=200)
    body_markdown: str | None = Field(None, max_length=20_000,
                                      description="a drafted letter to reuse instead of calling the letter writer")


class SummaryRequest(BaseModel):
    scope: str = "uk"
    weights: str | None = None
    top: int = Field(10, ge=3, le=20)


def _need_llm() -> None:
    if not llm.configured:
        raise HTTPException(503, "The agents need LLM_API_KEY and LLM_MODEL in .env (or run with --sample).")


def _queued(run_id: str, scope: str, label: str) -> None:
    """Write a 'queued' progress file so the page has something to poll at once."""
    C.RUNS_DIR.mkdir(parents=True, exist_ok=True)
    (C.RUNS_DIR / f"{run_id}.json").write_text(json.dumps(
        {"run_id": run_id, "scope": scope, "label": label, "status": "queued", "round": 0, "current": None,
         "steps": [], "result": None, "elapsed_s": 0}))


def _start(run_id: str, fn, **kw) -> None:
    def work():
        with _slots:
            try:
                fn(run_id=run_id, **kw)
            except Exception as e:  # noqa: BLE001 - the coordinator records failures; this is the last resort
                path = C.RUNS_DIR / f"{run_id}.json"
                state = json.loads(path.read_text()) if path.exists() else {"run_id": run_id, "steps": []}
                state.update(status="failed", result={"status": "failed", "headline": "The run failed.",
                                                      "notes": [f"{type(e).__name__}: {str(e)[:300]}"]})
                path.write_text(json.dumps(state))
    t = threading.Thread(target=work, name=f"run-{run_id}", daemon=True)
    _threads[run_id] = t
    t.start()


@router.post("/diagnose")
def diagnose(req: DiagnoseRequest) -> dict:
    """Start a point diagnosis; poll GET /api/runs/{run_id}."""
    _need_llm()
    try:
        point, _ = ev.resolve(req.query, req.lat, req.lon)
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    label = point.get("label") if point.get("kind") != "point" else (
        point.get("nearest_postcode") and f"near {point['nearest_postcode']}") or req.query or ""
    run_id = memory.create_run("point", query=req.query or f"{point['lat']:.5f},{point['lon']:.5f}", label=label,
                               location={"lat": point["lat"], "lon": point["lon"]})
    _queued(run_id, "point", label)
    _start(run_id, C.diagnose_point, query=req.query, lat=point["lat"] if not req.query else None,
           lon=point["lon"] if not req.query else None, audience=req.audience)
    return {"run_id": run_id, "status": "queued", "label": label, "location": {"lat": point["lat"], "lon": point["lon"]}}


@router.post("/council/zones/{zone_id}/diagnose")
def diagnose_zone(zone_id: str) -> dict:
    _need_llm()
    from coverage_detective.api.council import zone_detail

    z = zone_detail(zone_id)["zone"]  # 404s for an unknown zone
    run_id = memory.create_run("zone", query=zone_id, label=z["name"], location=z["centroid"])
    _queued(run_id, "zone", z["name"])
    _start(run_id, C.diagnose_zone, zone_id=zone_id)
    return {"run_id": run_id, "status": "queued", "label": z["name"], "zone_id": zone_id}


def _rounds(notes: list[dict], critiques: list[dict], beliefs: list[dict], evidence: list[dict]) -> list[dict]:
    """Round cards for the trace view, from the Meterless audit trail."""
    by_round: dict[int, dict] = {}

    def card(r) -> dict:
        r = int(r or 0)
        return by_round.setdefault(r, {"round": r, "tools": [], "evidence": [], "llm_calls": [], "guard": [],
                                       "revisions": [], "critiques": [], "coordinator": []})

    for n in notes:
        c = card(n.get("round"))
        t = n.get("type")
        if t == "tool_call":
            c["tools"].append({"tool": n.get("tool"), "args": n.get("args") or {}, "evidence_ids": n.get("evidence_ids") or [],
                               "summary": n.get("summary")})
        elif t in ("llm_call", "llm_error"):
            c["llm_calls"].append({"agent": n.get("by"), "model": (n.get("model") or "").split("/")[-1],
                                   "duration_ms": n.get("duration_ms"), "retries": len(n.get("retries") or []),
                                   "fallback": n.get("fallback"), "error": t == "llm_error", "purpose": n.get("purpose"),
                                   "summary": n.get("summary")})
        elif t == "guard":
            c["guard"].append({"agent": n.get("agent"), "kind": n.get("guard_kind"), "summary": n.get("summary")})
        elif t in ("coordinator", "fallback", "agent_error", "round"):
            c["coordinator"].append({"type": t, "summary": n.get("summary")})
    for e in evidence:
        card(e.get("round"))["evidence"].append(e.get("evidence_id"))
    for b in beliefs:
        for h in b.get("history") or []:
            card(h.get("round"))["revisions"].append({"belief": b.get("belief_id") or b.get("id"), "by": h.get("by"),
                                                      "from": h.get("previous"), "to": h.get("confidence"),
                                                      "reason": h.get("reason")})
    for c in critiques:
        card(c.get("round"))["critiques"].append({"target": c.get("belief_id") or c.get("target"), "verdict": c.get("verdict"),
                                                  "reason": c.get("reason"), "requested_checks": c.get("requested_checks") or [],
                                                  "suggested_confidence": c.get("suggested_confidence")})
    return [by_round[r] for r in sorted(by_round, key=lambda r: r or 99)]  # "final" (round 0) last


def run_detail(run_id: str) -> dict:
    """Trace, evidence and World Model: from the export when finished, else live from Meterless."""
    full_path = C.RUNS_DIR / f"{run_id}.full.json"
    if full_path.exists():
        full = json.loads(full_path.read_text())
    else:
        full = {"audit": memory.audit(run_id), "notes": memory.notes(run_id), "critiques": memory.critiques(run_id),
                "beliefs": memory.beliefs(run_id), "evidence": memory.evidence(run_id), "world": memory.world_for_run(run_id),
                "memory_mode": memory.mode}
    evidence = [{k: e.get(k) for k in ("evidence_id", "source", "data_period", "as_of", "query_or_url", "cell_ids", "finding",
                                       "value", "sample_size", "strength", "bears_on", "tool", "round", "zetaris")}
                for e in full.get("evidence") or []]
    return {"trace": {"rounds": _rounds(full.get("notes") or [], full.get("critiques") or [], full.get("beliefs") or [],
                                        full.get("evidence") or []),
                      "audit": [{k: a.get(k) for k in ("seq", "at", "action", "actor", "kind", "ref", "round", "summary",
                                                       "old_confidence", "new_confidence", "reason")}
                                for a in full.get("audit") or []]},
            "evidence": evidence, "world": full.get("world") or {"entities": [], "relationships": []},
            "memory_mode": full.get("memory_mode")}


@router.get("/runs/{run_id}")
def run(run_id: str, detail: bool = False) -> dict:
    """Progress (always) and, once finished or with ?detail=1, trace, evidence and World Model."""
    path = C.RUNS_DIR / f"{run_id}.json"
    if not path.exists():
        raise HTTPException(404, f"No run {run_id!r}")
    state = json.loads(path.read_text())
    steps = [{k: v for k, v in s.items() if k != "t"} for s in state.get("steps", [])]
    out = {"run_id": run_id, "scope": state.get("scope"), "label": state.get("label"), "status": state.get("status"),
           "round": state.get("round"), "current": state.get("current"), "elapsed_s": state.get("elapsed_s"),
           "steps": steps, "result": state.get("result")}
    if detail or out["status"] not in ("queued", "running"):
        out.update(run_detail(run_id))
    return out


def _finished(run_id: str) -> dict:
    """The result of a finished run; 404 if unknown, 409 if still running."""
    path = C.RUNS_DIR / f"{run_id}.json"
    if not path.exists():
        raise HTTPException(404, f"No run {run_id!r}")
    result = json.loads(path.read_text()).get("result")
    if not result or result.get("status") not in ("complete", "partial"):
        raise HTTPException(409, "The run has not finished yet.")
    return result


def _letter_guard(run_id: str, since: int = 0) -> list[str]:
    """Guard notes the letter writer made since `since` (in notes), so one draft is not credited with another's."""
    return [n.get("summary") for n in memory.notes(run_id)[since:]
            if n.get("type") == "guard" and n.get("agent") == "letter_writer"]


@router.post("/runs/{run_id}/letter")
def letter(run_id: str, req: LetterRequest) -> dict:
    """Draft an evidence letter from a finished point run (the letter writer, checked by the guard)."""
    _need_llm()
    result = _finished(run_id)
    case = Case(run_id=run_id, scope=result.get("scope", "point"), point=result.get("location") or {}, memory=memory)
    try:
        out = A.write_letter(case, req.recipient, result)
    except LLMError as e:
        raise HTTPException(502, f"The letter writer is unavailable: {str(e)[:160]}") from e
    return {**out.model_dump(), "run_id": run_id, "guard": _letter_guard(run_id)}


@router.get("/outreach/recipients")
def outreach_recipients(district: str | None = Query(None, max_length=120,
                                                     description="local authority name, as postcodes.io returns it")) -> dict:
    """Who a diagnosis can be sent to, and how each of them can be reached."""
    return O.recipients(district)


@router.post("/runs/{run_id}/email")
def email(run_id: str, req: EmailRequest) -> dict:
    """The email for one recipient: the letter writer drafts for that council or network, the guard checks
    it, and outreach.compose adds the address, the signature and the mailto link (code, not the model)."""
    result = _finished(run_id)
    district = (result.get("location") or {}).get("admin_district") or (result.get("zone") or {}).get("council")
    try:
        contact = O.contact_for(district, req.to)
    except LookupError as e:
        raise HTTPException(422, str(e)) from e
    if req.body_markdown:
        drafted = {"subject": req.subject or "", "body_markdown": req.body_markdown}
        guard: list[str] = []            # no model, so no guard ran on this text
    else:
        _need_llm()
        before = len(memory.notes(run_id))
        case = Case(run_id=run_id, scope=result.get("scope", "point"), point=result.get("location") or {}, memory=memory)
        try:
            wire = A.write_letter(case, "council" if contact.kind == "council" else "operator", result,
                                  addressee=contact.name)
        except LLMError as e:
            raise HTTPException(502, f"The letter writer is unavailable: {str(e)[:160]}") from e
        drafted = {"subject": wire.subject, "body_markdown": wire.body_markdown}
        guard = _letter_guard(run_id, before)
    out = O.compose(result, drafted, contact, sent_by=req.sent_by)
    return {**out.to_dict(), "contact": contact.to_dict(), "district": district, "run_id": run_id,
            "guard": guard, "markdown": drafted["body_markdown"]}


@router.post("/council/summary")
def priority_summary(req: SummaryRequest) -> dict:
    """A priority summary of the top zones in a scope, with any agent diagnoses of them from Meterless."""
    _need_llm()
    from coverage_detective.api.council import scopes, zones

    ranked = zones(scope=req.scope, weights=req.weights, limit=req.top)
    if not ranked["zones"]:
        raise HTTPException(404, f"No gap zones in {req.scope}")
    names = {s["scope"]: s["name"] for s in [scopes()["uk"], *scopes()["nations"], *scopes()["councils"]]}
    scope_name = names.get(req.scope, req.scope)
    diagnoses: dict[str, dict] = {}
    for z in ranked["zones"]:
        near = [d for d in memory.diagnoses_near(z["centroid"]["lat"], z["centroid"]["lon"], km=1.0)
                if d.get("zone_id") == z["zone_id"]]
        if near:
            diagnoses[z["zone_id"]] = max(near, key=lambda d: d.get("created_at") or "")
    run_id = memory.create_run("summary", query=req.scope, label=f"Priority summary: {scope_name}")
    case = Case(run_id=run_id, scope="summary", point={"label": scope_name}, memory=memory)
    try:
        out = A.write_priority_summary(case, scope_name, ranked["zones"], diagnoses)
    except LLMError as e:
        raise HTTPException(502, f"The advisor is unavailable: {str(e)[:160]}") from e
    title = f"Mobile coverage gaps in {scope_name}: priorities"
    memory.update_run(run_id, status="complete", result={"title": title, **out.model_dump()})
    guard = [n.get("summary") for n in memory.notes(run_id) if n.get("type") == "guard"]
    rows = [{k: z[k] for k in ("zone_id", "rank", "name", "council", "people", "people_by_reason", "tests", "confidence")}
            | {"diagnosis": (diagnoses.get(z["zone_id"]) or {}).get("headline")} for z in ranked["zones"]]
    return {"title": title, **out.model_dump(), "run_id": run_id, "scope": req.scope, "zones": [z["zone_id"] for z in ranked["zones"]],
            "zone_rows": rows,
            "diagnosed": sorted(diagnoses), "guard": guard}
