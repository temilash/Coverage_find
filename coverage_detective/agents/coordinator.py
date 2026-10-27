"""Coordinator: runs the investigation loop over the shared case file in Meterless.

    diagnose_point(query="LA22 9JY")  or  diagnose_point(lat=54.445, lon=-3.105)
    diagnose_zone("Z00603")

Loop (max 3 rounds): investigate -> diagnose -> critique -> (if the critic requested checks and
round < 3: next round) -> act (advisor; for zones the fix planner, reviewed by the critic).
Agents do not message each other; each reads and writes Meterless. The coordinator also
writes the World Model (place or zone, its sites per operator with the line-of-sight result,
warnings, events), looks up prior diagnoses nearby, and mirrors progress per agent call and
tool call to data/runs/<run_id>.json so the UI can show a progress strip.

If an agent fails twice the run ends "partial", built from the evidence alone.
"""

from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from coverage_detective import evidence as ev
from coverage_detective.agents import agents as A
from coverage_detective.agents.llm import LLMError
from coverage_detective.agents.tools import OPERATORS, Case, run_tool
from coverage_detective.config import ROOT
from coverage_detective.memory.meterless import Meterless, memory as default_memory

RUNS_DIR = ROOT / "data" / "runs"
MAX_ROUNDS = 3
LIMITATIONS = ev.LIMITATIONS


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class Progress:
    """Fine-grained progress (one step per agent call and tool call), mirrored to disk and Meterless."""

    def __init__(self, run_id: str, scope: str, label: str, memory: Meterless):
        self.run_id, self.memory = run_id, memory
        self.lock = threading.Lock()
        self.t0 = time.monotonic()
        self.state = {"run_id": run_id, "scope": scope, "label": label, "status": "running", "started": _now(),
                      "updated": _now(), "round": 0, "current": None, "steps": [], "result": None}
        RUNS_DIR.mkdir(parents=True, exist_ok=True)
        self.path = RUNS_DIR / f"{run_id}.json"
        self._write()

    def _write(self) -> None:
        self.state["updated"] = _now()
        self.state["elapsed_s"] = round(time.monotonic() - self.t0, 1)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, default=str))
        tmp.replace(self.path)

    def start(self, agent: str, round: int, kind: str, label: str) -> int:
        with self.lock:
            self.state["round"] = max(self.state["round"], round or 0)
            step = {"n": len(self.state["steps"]) + 1, "agent": agent, "round": round, "kind": kind, "label": label,
                    "status": "running", "started": _now(), "t": time.monotonic()}
            self.state["steps"].append(step)
            self.state["current"] = {"agent": agent, "round": round, "label": label}
            self._write()
            return step["n"]

    def _finish(self, n: int, status: str, summary: str) -> None:
        with self.lock:
            step = self.state["steps"][n - 1]
            step.update(status=status, summary=summary, ended=_now(), duration_ms=round((time.monotonic() - step.pop("t")) * 1000))
            self._write()
        self.memory.update_run(self.run_id, progress={"agent": step["agent"], "round": step["round"], "kind": step["kind"],
                                                      "label": step["label"], "status": status, "summary": summary,
                                                      "duration_ms": step["duration_ms"]}, by="coordinator")

    def done(self, n: int, summary: str = "") -> None:
        self._finish(n, "done", summary)

    def fail(self, n: int, summary: str = "") -> None:
        self._finish(n, "failed", summary)

    def phase(self, agent: str, round: int, label: str) -> None:
        """A step with no duration (e.g. 'round 2 starts')."""
        self.done(self.start(agent, round, "phase", label), label)

    def finish(self, status: str, result: dict) -> None:
        with self.lock:
            self.state.update(status=status, current=None, result=result)
            self._write()


def _attempt(case: Case, agent: str, fn, *args, **kw):
    """Run an agent; one retry; None if it fails twice (the run becomes partial)."""
    for attempt in (1, 2):
        try:
            return fn(case, *args, **kw)
        except (LLMError, ValueError, KeyError, TypeError) as e:
            case.memory.add_note(case.run_id, "agent_error", f"{agent} failed (attempt {attempt}): {type(e).__name__}: "
                                 f"{str(e)[:200]}", by="coordinator", agent=agent, attempt=attempt)
    return None


# ------------------------------------------------------------------ World Model


def _world_point(case: Case) -> str:
    m, r = case.memory, case.run_id
    p = case.point
    place = m.upsert_entity("place", f"{case.lat:.5f},{case.lon:.5f}", name=p.get("label"),
                            attrs={"lat": case.lat, "lon": case.lon, "cell_id": case.cell_id, "postcode": p.get("postcode")
                                   or p.get("nearest_postcode"), "district": p.get("admin_district"), "country": p.get("country")},
                            run_id=r)
    case.cache["place_id"] = place["id"]
    if p.get("admin_district"):
        area = m.upsert_entity("area", p["admin_district"], name=p["admin_district"], attrs={"kind": "local authority"}, run_id=r)
        m.relate(place["id"], "in_area", area["id"], run_id=r)
    return place["id"]


def _world_evidence(case: Case, place_id: str) -> None:
    """Sites per operator with the line-of-sight result, and live warnings, from the evidence."""
    m, r = case.memory, case.run_id
    evid = A.evidence_map(case)
    for op in OPERATORS:
        e = evid.get(f"los_{op.lower()}")
        if not e:
            continue
        v = e.get("value") or {}
        site = m.upsert_entity("site", v["site_id"], attrs={"operators": v.get("operators_at_site"),
                                                           "operators_confirmed": v.get("operators_confirmed")}, run_id=r)
        oper = m.upsert_entity("operator", op, name=op, run_id=r)
        m.relate(place_id, "nearest_site", site["id"], context={"type": "operator", "key": op}, run_id=r,
                 attrs={"operator": op, "line_of_sight": v.get("los_class"), "obstruction_m": v.get("max_obstruction_m"),
                        "distance_m": v.get("path_length_m"), "evidence": e["evidence_id"]})
        m.relate(site["id"], "operated_by" if op in (v.get("operators_confirmed") or []) else "inferred_operator",
                 oper["id"], context={"type": "operator", "key": op}, run_id=r)
    warn = evid.get("met_office_warnings")
    for w in ((warn or {}).get("value") or {}).get("warnings", [])[:5]:
        we = m.upsert_entity("warning", f"metoffice:{w.get('level')}:{','.join(w.get('types', []))}:{w.get('valid_to')}",
                             name=w.get("headline"), attrs={k: w.get(k) for k in ("level", "types", "valid_from", "valid_to",
                                                                                   "district_listed")}, run_id=r)
        m.relate(place_id, "under_warning", we["id"], run_id=r, attrs={"district_listed": w.get("district_listed")})


# ------------------------------------------------------------------ the loop


def _loop(case: Case, prog: Progress) -> tuple[int, bool]:
    """Investigate / diagnose / critique rounds. Returns (rounds, ok)."""
    requested: list[dict] = []
    for rnd in range(1, MAX_ROUNDS + 1):
        prog.phase("coordinator", rnd, f"round {rnd}" + (f": {len(requested)} checks requested by the critic" if requested else ""))
        case.memory.add_note(case.run_id, "round", f"round {rnd} starts", by="coordinator", round=rnd,
                             requested=requested)
        if _attempt(case, "investigator", A.investigate, rnd, requested) is None:
            return rnd, False
        if _attempt(case, "diagnostician", A.diagnose, rnd) is None:
            return rnd, False
        final = rnd == MAX_ROUNDS
        requested = _attempt(case, "critic", A.critique, rnd, final)
        if requested is None:
            return rnd, False
        if not requested:
            return rnd, True
    return MAX_ROUNDS, True


def _beliefs_out(case: Case) -> list[dict]:
    out = []
    for b in sorted(case.memory.beliefs(case.run_id), key=lambda b: -b["confidence"]):
        out.append({"id": b["belief_id"], "cause": b["cause"], "confidence": b["confidence"], "status": b.get("status", "active"),
                    "supporting": b.get("supporting"), "contradicting": b.get("contradicting"), "explanation": b.get("explanation"),
                    "history": b.get("history")})
    return out


def _critic_summary(case: Case) -> list[dict]:
    return [{"round": c.get("round"), "target": c.get("belief_id") or c.get("target"), "verdict": c["verdict"],
             "reason": c.get("reason"), "requested_checks": c.get("requested_checks") or [], "issues": c.get("issues"),
             "suggested_confidence": c.get("suggested_confidence")} for c in case.memory.critiques(case.run_id)]


def _partial(case: Case, rounds: int, why: str) -> dict:
    """A result from the evidence alone, when an agent failed twice."""
    evid = A.evidence_map(case)
    comp = evid.get("network_comparison") or evid.get("zone_overview")
    beliefs = _beliefs_out(case)
    top = next((b for b in beliefs if b["status"] == "active"), None)
    headline = (f"Partial result: {top['cause'].replace('_', ' ')} is the leading belief so far ({top['confidence']:.2f}), "
                "not yet reviewed in full." if top else "Partial result: the evidence gathered is listed below; the agents "
                "could not complete a diagnosis.")
    return {"headline": headline, "explanation": comp["finding"] if comp else None, "hypotheses": beliefs,
            "status": "partial", "rounds": rounds, "notes": [why]}


def diagnose_point(query: str | None = None, lat: float | None = None, lon: float | None = None,
                   audience: str = "resident", memory: Meterless = default_memory, run_id: str | None = None,
                   replay=None) -> dict:
    t0 = time.monotonic()
    point, _ = ev.resolve(query, lat, lon)
    label = point.get("label") if point.get("kind") != "point" else (point.get("nearest_postcode") and f"near {point['nearest_postcode']}") or query or point.get("label")
    run_id = run_id or memory.create_run("point", query=query or f"{point['lat']:.5f},{point['lon']:.5f}", label=label,
                                         location={"lat": point["lat"], "lon": point["lon"]})
    prog = Progress(run_id, "point", label or "", memory)
    case = Case.for_point(run_id, query, lat, lon, memory=memory)
    case.progress = prog
    if replay:
        case.cache["replay"] = replay
    status, notes = "complete", []
    try:
        place_id = _world_point(case)
        run_tool(case, "prior_diagnoses_nearby", {}, round=1, by="coordinator")
        rounds, ok = _loop(case, prog)
        _world_evidence(case, place_id)
        if not ok:
            result = _partial(case, rounds, "an agent failed twice; result built from the evidence alone")
            status = "partial"
        else:
            adv = _attempt(case, "advisor", A.advise, audience)
            if adv is None:
                result, status = _partial(case, rounds, "the advisor failed twice"), "partial"
            else:
                evid = A.evidence_map(case)
                comp = (evid.get("network_comparison") or {}).get("value") or {}
                result = {"headline": adv.headline, "explanation": adv.explanation, "cited_evidence": adv.cited_evidence,
                          "hypotheses": _beliefs_out(case), "ruled_out": [r.model_dump() for r in adv.ruled_out],
                          "actions": [a.model_dump() for a in adv.actions],
                          "best_network": {**adv.best_network.model_dump(), "rule_based": comp.get("likely_best"),
                                           "operators": comp.get("operators")},
                          "limitations": [*operator_limits(evid), adv.limitations, *LIMITATIONS], "rounds": rounds,
                          "status": "complete"}
        result.update(run_id=run_id, scope="point", query=query, audience=audience,
                      location={k: point.get(k) for k in ("label", "lat", "lon", "postcode", "nearest_postcode",
                                                          "admin_district", "region", "country")} | {"cell_id": case.cell_id},
                      critiques=_critic_summary(case))
        if (headline := no_data_headline(result, A.evidence_map(case))) and status == "complete":
            result["advisor_headline"], result["headline"] = result["headline"], headline
            memory.add_note(run_id, "coordinator", "headline set from the top belief (insufficient data): " + headline,
                            by="coordinator")
        result["wording_check"] = wording_check(result)
        top = next((h for h in result["hypotheses"] if h["status"] == "active"), None)
        memory.record_diagnosis(run_id, case.lat, case.lon, cell_id=case.cell_id, scope="point", headline=result["headline"],
                                causes=[{"cause": h["cause"], "confidence": h["confidence"]} for h in result["hypotheses"]
                                        if h["status"] == "active"], confidence=top["confidence"] if top else 0.3, status=status)
        memory.add_event("diagnosis_completed", place_id, {"headline": result["headline"], "status": status,
                                                           "top_cause": top and top["cause"]}, run_id=run_id)
    except Exception as e:  # noqa: BLE001 - never leave a run hanging
        status = "failed"
        result = {"run_id": run_id, "scope": "point", "query": query, "status": "failed", "headline": "The run failed.",
                  "hypotheses": _beliefs_out(case), "notes": [f"{type(e).__name__}: {str(e)[:300]}"], "rounds": 0,
                  "location": {"lat": case.lat, "lon": case.lon}}
    result["status"] = status
    result["elapsed_s"] = round(time.monotonic() - t0, 1)
    if replay:
        result.update(example_id=replay.example_id, replayed=replay.mode == "replay", rules_only=replay.mode == "rules")
    memory.update_run(run_id, status=status, result=result)
    export(run_id, memory)  # before the progress says "finished", so a poll never reads a half-written export
    prog.finish(status, result)
    return result


def diagnose_zone(zone_id: str, memory: Meterless = default_memory, run_id: str | None = None, replay=None) -> dict:
    from coverage_detective.api.council import zone_detail

    t0 = time.monotonic()
    detail = zone_detail(zone_id)
    z = detail["zone"]
    run_id = run_id or memory.create_run("zone", query=zone_id, label=z["name"], location=z["centroid"])
    prog = Progress(run_id, "zone", z["name"], memory)
    case = Case(run_id=run_id, scope="zone", point={**z["centroid"], "label": z["name"]}, zone_id=zone_id, memory=memory)
    case.cache["zone"] = detail
    case.progress = prog
    if replay:
        case.cache["replay"] = replay
    status = "complete"
    try:
        zone_ent = memory.upsert_entity("zone", zone_id, name=z["name"], attrs={"people": z["people"], "cells": z["cells"],
                                        "council": z["council"], "nation": z["nation"],
                                        "zone_type": z["zone_type"], "reasons": z.get("reasons")}, run_id=run_id)
        council = memory.upsert_entity("area", z["council"], name=z["council"], attrs={"kind": "local authority"}, run_id=run_id)
        memory.relate(zone_ent["id"], "in_area", council["id"], run_id=run_id)
        rounds, ok = _loop(case, prog)
        plan, review, revisions = None, None, 0
        if ok:
            for attempt in range(2):  # plan, review, at most one revision
                plan = _attempt(case, "fix_planner", A.plan_fix, rounds, review)
                if plan is None:
                    ok = False
                    break
                review = _attempt(case, "critic", A.review_fix, rounds, plan)
                if review is None or review.verdict == "approve":
                    break
                revisions += 1
        evid = A.evidence_map(case)
        for i in (1, 2, 3):
            c = evid.get(f"cand_{i}")
            if c:
                v = c["value"]
                site = memory.upsert_entity("candidate_site", f"{zone_id}:cand_{i}", attrs=v, run_id=run_id)
                memory.relate(zone_ent["id"], "candidate_site", site["id"], run_id=run_id,
                              attrs={"zone_people_gaining": v["zone_people_gaining"], "evidence": f"cand_{i}"})
        if not ok:
            result, status = _partial(case, rounds, "an agent failed twice; result built from the evidence alone"), "partial"
        else:
            adv = _attempt(case, "advisor", A.advise_zone)
            result = {"headline": adv.headline if adv else "Zone diagnosis without the advisor's summary",
                      "explanation": adv.explanation if adv else None, "cited_evidence": adv.cited_evidence if adv else [],
                      "hypotheses": _beliefs_out(case), "fix_plan": {**plan, "review": review.model_dump() if review else None,
                                                                     "revisions": revisions} if plan else None,
                      "limitations": [adv.limitations] if adv else [], "rounds": rounds,
                      "status": "complete" if adv else "partial"}
            status = result["status"]
            if plan:
                memory.add_event("fix_plan_proposed", zone_ent["id"], {"fix_type": plan["fix_type"],
                                 "candidate": plan.get("candidate"), "approved": bool(review and review.verdict == "approve")},
                                 run_id=run_id)
        result.update(run_id=run_id, scope="zone", query=zone_id, zone=z, zone_type=z["zone_type"],
                      location={**z["centroid"], "label": z["name"]}, critiques=_critic_summary(case))
        result["wording_check"] = wording_check(result)
        if result["wording_check"]["applies"]:
            memory.add_note(run_id, "wording_check", "opportunity wording " + ("ok" if result["wording_check"]["ok"] else
                            "problems: " + "; ".join(f"{f}: {w}" for f, w in result["wording_check"]["forbidden"])),
                            by="coordinator", **result["wording_check"])
        top = next((h for h in result["hypotheses"] if h["status"] == "active"), None)
        memory.record_diagnosis(run_id, z["centroid"]["lat"], z["centroid"]["lon"], scope="zone", zone_id=zone_id,
                                headline=result["headline"], causes=[{"cause": h["cause"], "confidence": h["confidence"]}
                                                                     for h in result["hypotheses"] if h["status"] == "active"],
                                confidence=top["confidence"] if top else 0.3, status=status)
        memory.add_event("diagnosis_completed", zone_ent["id"], {"headline": result["headline"], "status": status}, run_id=run_id)
    except Exception as e:  # noqa: BLE001
        status = "failed"
        result = {"run_id": run_id, "scope": "zone", "query": zone_id, "status": "failed", "headline": "The run failed.",
                  "hypotheses": _beliefs_out(case), "notes": [f"{type(e).__name__}: {str(e)[:300]}"], "rounds": 0,
                  "location": z["centroid"]}
    result["status"] = status
    result["elapsed_s"] = round(time.monotonic() - t0, 1)
    if replay:
        result.update(example_id=replay.example_id, replayed=replay.mode == "replay", rules_only=replay.mode == "rules")
    memory.update_run(run_id, status=status, result=result)
    export(run_id, memory)  # before the progress says "finished", so a poll never reads a half-written export
    prog.finish(status, result)
    return result


def no_data_headline(result: dict, evid: dict) -> str | None:
    """When the top belief is insufficient_data and nothing is measured here, the headline says so plainly
    (code, not the advisor's wording): "Not enough data to say why: ..."."""
    active = [h for h in result.get("hypotheses") or [] if h["status"] == "active"]
    ins = next((h for h in active if h["cause"] == "insufficient_data"), None)
    if not ins or ins.get("confidence", 0) < max(h.get("confidence", 0) for h in active):  # top, or tied for top
        return None
    tests = lambda i: ((evid.get(i) or {}).get("sample_size") or {}).get("tests") or 0  # noqa: E731
    if tests("speed_cell") or tests("speed_1km"):
        return None
    why = ["there are no measured speeds at this point or within 1 km"]
    near = evid.get("nearest_site")
    if near and near.get("value") is None:
        why.append("no mast sites in our data within 10 km, so operators could not be identified")
    elif not ((evid.get("operators_5km") or {}).get("value") or {}).get("operators"):
        why.append("operators could not be identified for the sites nearby")
    return "Not enough data to say why: " + " and ".join(why) + "."


def operator_limits(evid: dict) -> list[str]:
    """A fixed line when no operator can be identified near the point (code, not the model's wording)."""
    near = evid.get("nearest_site")
    v = (evid.get("operators_5km") or {}).get("value") or {}
    if near and near.get("value") is None:
        where = "no approximate mast sites were found within 10 km in our data"
    elif v.get("operator_data_status") in ("no_sites_found", "unknown") and not v.get("operators"):
        where = "the approximate mast sites nearby have no known operator"
    else:
        where = None
    if where:
        return [f"Operators could not be identified: {where}. This is not the same as no coverage: crowd-sourced "
                "site data is incomplete; check the address on Ofcom's mobile coverage checker."]
    return []


FORBIDDEN_FOR_OPPORTUNITIES = [r"\bgaps?\b", r"\bno coverage\b", r"\bno service\b", r"\bno signal\b"]


def wording_check(result: dict) -> dict:
    """For an improvement opportunity: the agents' text says "opportunity" and never "gap", "no coverage" or
    "no service". Reported (and noted in Meterless), not rewritten."""
    if result.get("scope") == "point":  # points: never "no coverage" / "no service"
        fields = {"headline": result.get("headline"), "explanation": result.get("explanation"),
                  **{f"belief.{h['cause']}": h.get("explanation") for h in result.get("hypotheses") or []},
                  **{f"action.{i}": a.get("text") for i, a in enumerate(result.get("actions") or [])}}
        forbidden = [(k, m.group(0)) for k, v in fields.items() if v for pat in FORBIDDEN_FOR_OPPORTUNITIES[1:]
                     for m in re.finditer(pat, v, re.IGNORECASE)]
        return {"applies": True, "ok": not forbidden, "forbidden": forbidden}
    if result.get("zone_type") != "opportunity":
        return {"applies": False}
    fix = result.get("fix_plan") or {}
    fields = {"headline": result.get("headline"), "explanation": result.get("explanation"), "fix_plan.why": fix.get("why"),
              **{f"fix_plan.assumptions.{i}": a for i, a in enumerate(fix.get("assumptions") or [])},
              **{f"belief.{h['cause']}": h.get("explanation") for h in result.get("hypotheses") or []}}
    allowed = re.compile(r"\bnot an? (confirmed )?gap\b", re.IGNORECASE)  # "an opportunity, not a confirmed gap" is the asked-for wording
    forbidden = [(k, m.group(0)) for k, v in fields.items() if v for pat in FORBIDDEN_FOR_OPPORTUNITIES
                 for m in re.finditer(pat, allowed.sub("", v), re.IGNORECASE)]
    says = any(re.search(r"opportunit", v or "", re.IGNORECASE) for v in (fields["headline"], fields["explanation"], fields["fix_plan.why"]))
    return {"applies": True, "ok": says and not forbidden, "mentions_opportunity": says, "forbidden": forbidden}


def export(run_id: str, memory: Meterless = default_memory) -> Path:
    """Everything about a run in one file: result, audit trail, evidence, notes, critiques, World Model."""
    run = memory.get_run(run_id) or {}
    full = {"run": run, "result": run.get("result"), "audit": memory.audit(run_id), "evidence": memory.evidence(run_id),
            "beliefs": memory.beliefs(run_id), "critiques": memory.critiques(run_id), "notes": memory.notes(run_id),
            "world": memory.world_for_run(run_id), "memory_mode": memory.mode}
    path = RUNS_DIR / f"{run_id}.full.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(full, indent=1, default=str))
    tmp.replace(path)  # atomic: readers see the old file or the whole new one
    return path
