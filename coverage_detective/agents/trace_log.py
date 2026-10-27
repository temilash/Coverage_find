"""One JSONL agent trace per run: logs/run_<run_id>.jsonl, also printed to stdout when the run ends.

Built from what the run already recorded (no second logging system): the Meterless audit trail
(H-MEM writes: evidence, beliefs and their revisions, critiques), the notes (model calls with the
provider and the answering model, tool calls, guard interventions, rounds, fallbacks), the Zetaris
queries attached to each evidence item (rows, time), the World Model writes, the case-file reads the
client recorded per agent, and the progress file. Every line has the same fields:

    timestamp, run_id, agent_name, action, target_agent, input_summary, output_summary, provider,
    model_requested, model_answered, receipt_id, confidence, retry_count, status

target_agent is the handoff: the agent that reads what this line wrote from the shared case file
(agents never message each other).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from coverage_detective.config import ROOT

LOGS = ROOT / "logs"
FIELDS = ("timestamp", "run_id", "agent_name", "action", "target_agent", "input_summary", "output_summary", "provider",
          "model_requested", "model_answered", "receipt_id", "confidence", "retry_count", "status")
TABLE = re.compile(r"\b(CD_[A-Z_]+\.[a-z0-9_]+)")
NEXT = {"investigator": "diagnostician", "coordinator": "diagnostician", "diagnostician": "critic"}


def _line(run_id: str, at: str | None, agent: str | None, action: str, **kw) -> dict:
    out = {k: None for k in FIELDS} | {"status": "ok"}
    out.update(timestamp=at, run_id=run_id, agent_name=agent, action=action, **kw)
    for k in ("input_summary", "output_summary"):
        if isinstance(out[k], str) and len(out[k]) > 300:
            out[k] = out[k][:297] + "..."
    return out


def _checks(checks: list[dict]) -> str:
    """[{"tool": "line_of_sight", "operator": "EE"}] -> "line_of_sight(EE)"."""
    return ", ".join(x["tool"] + (f"({x['operator']})" if x.get("operator") else "") for x in checks)


def lines(full: dict) -> list[dict]:
    """The trace lines of an exported run (data/runs/<id>.full.json), in time order."""
    run = full.get("run") or {}
    rid = run.get("run_id") or run.get("id") or ""
    result = full.get("result") or {}
    out: list[dict] = []
    sql = {q["id"]: q.get("sql") or "" for e in full.get("evidence") or [] for q in e.get("zetaris") or []}
    sample = bool(result.get("example_id") or result.get("rules_only"))  # sample data: DuckDB stands in for Zetaris
    seen: set = set()
    for a in full.get("audit") or []:
        kind, at, who = a.get("kind"), a.get("at"), a.get("actor")
        if kind == "run" and a.get("action") == "create":
            out.append(_line(rid, at, "coordinator", "run_start", input_summary=a.get("summary"), target_agent="investigator"))
        elif kind == "evidence":
            out.append(_line(rid, at, who, "hmem_write_evidence", target_agent=NEXT.get(who, "diagnostician"),
                             input_summary=a.get("ref"), output_summary=a.get("summary"), confidence=a.get("new_confidence")))
        elif kind == "belief":
            revised = a.get("action") == "update"
            out.append(_line(rid, at, who, "hmem_revise_belief" if revised else "hmem_write_belief",
                             target_agent="diagnostician" if who == "critic" else "critic", input_summary=a.get("ref"),
                             output_summary=(f"{a.get('old_confidence')} -> {a.get('new_confidence')}: " if revised else "")
                             + str(a.get("reason") or a.get("summary") or ""), confidence=a.get("new_confidence")))
        elif kind == "diagnosis":
            out.append(_line(rid, at, "coordinator", "hmem_write_diagnosis", output_summary=a.get("summary"),
                             confidence=a.get("new_confidence")))
    for c in full.get("critiques") or []:
        checks = c.get("requested_checks") or []
        out.append(_line(rid, c.get("created_at"), "critic", f"critique_{c.get('verdict')}",
                         target_agent="investigator" if checks else "diagnostician" if c.get("verdict") != "approve" else "advisor",
                         input_summary=f"belief {c.get('belief_id')}",
                         output_summary=(c.get("reason") or "") + (f" | requested: {_checks(checks)}" if checks else ""),
                         confidence=c.get("suggested_confidence")))
    for n in full.get("notes") or []:
        t, at, by = n.get("type"), n.get("created_at"), n.get("by")
        if t in ("llm_call", "llm_error"):
            rec = n.get("receipt") or {}
            out.append(_line(rid, at, by, "model_call" + (f" ({n['purpose']})" if n.get("purpose") else ""),
                             input_summary=f"round {n.get('round')}" + (", thinking on" if n.get("thinking") else ""),
                             output_summary=n.get("summary"), provider=n.get("provider"),
                             model_requested=n.get("model_requested") or n.get("model"),
                             model_answered=n.get("model_answered"), receipt_id=rec.get("receipt_id"),
                             retry_count=len(n.get("retries") or []), status="error" if t == "llm_error" else "ok"))
        elif t == "tool_call":
            out.append(_line(rid, at, by, f"tool:{n.get('tool')}", input_summary=json.dumps(n.get("args") or {}),
                             output_summary=", ".join(n.get("evidence_ids") or []) or n.get("summary"),
                             target_agent=NEXT.get(by, "diagnostician")))
            for q in n.get("zetaris_queries") or []:  # the queries this tool call ran (shared ones are listed once)
                if q.get("id") in seen:
                    continue
                seen.add(q.get("id"))
                out.append(_line(rid, at, by, "zetaris_query" + (" (sample mode: DuckDB over the extracts)" if sample else ""),
                                 input_summary=f"{q.get('id')}: " + ", ".join(dict.fromkeys(TABLE.findall(sql.get(q.get("id"), "")))),
                                 output_summary=f"{q.get('rows')} rows in {q.get('duration_ms')} ms"))
        elif t == "guard":
            out.append(_line(rid, at, "guard", f"guard:{n.get('guard_kind')}", target_agent=n.get("agent"),
                             output_summary=n.get("summary"), status="intervened"))
        elif t == "round":
            out.append(_line(rid, at, "coordinator", "round_start", target_agent="investigator",
                             input_summary=n.get("summary"),
                             output_summary=f"{len(n.get('requested') or [])} checks requested by the critic"))
        elif t in ("provider_fallback", "fallback", "agent_error", "replay_missing", "coordinator"):
            out.append(_line(rid, at, by or "coordinator", t, target_agent=n.get("agent"), output_summary=n.get("summary"),
                             provider=n.get("provider"), status="fallback" if "fallback" in t else
                             "error" if t in ("agent_error", "replay_missing") else "ok"))
    world = full.get("world") or {}
    for x in world.get("entities") or []:
        src = x.get("source") or {}
        out.append(_line(rid, src.get("at") or x.get("createdAt"), src.get("by"), "world_write_entity",
                         input_summary=x.get("id"), output_summary=x.get("name") or x.get("type")))
    for x in world.get("relationships") or []:
        src = x.get("source") or {}
        out.append(_line(rid, src.get("at") or x.get("validFrom"), src.get("by"), "world_write_relationship",
                         input_summary=f"{x.get('from')} -{x.get('type')}-> {x.get('to')}",
                         output_summary=json.dumps(x.get("attrs"))[:200] if x.get("attrs") else None))
    for r in full.get("reads") or []:
        out.append(_line(rid, r.get("at"), r.get("agent"), "world_read" if r["read"].startswith("world") else "hmem_read",
                         input_summary=r["read"], output_summary=f"{r['count']} records" + (f" (read {r['times']}x)" if r.get("times", 1) > 1 else "")))
    out.sort(key=lambda x: (x["timestamp"] or "", not x["action"].startswith("model_call")))  # a call before its output
    out.append(_line(rid, (out[-1]["timestamp"] if out else None), "coordinator", "run_end",
                     output_summary=result.get("headline"), status=result.get("status") or run.get("status"),
                     input_summary=f"{result.get('rounds')} rounds" + (" (recorded replies)" if result.get("replayed") else
                                                                      " (rules only, no model)" if result.get("rules_only") else "")))
    return out


def write(full: dict, echo: bool = True) -> Path:
    """logs/run_<run_id>.jsonl (and each line on stdout)."""
    rows = lines(full)
    rid = rows[0]["run_id"] if rows else "unknown"
    LOGS.mkdir(parents=True, exist_ok=True)
    path = LOGS / f"{rid if rid.startswith('run_') else 'run_' + rid}.jsonl"  # run ids already start with run_
    text = "".join(json.dumps(r, default=str) + "\n" for r in rows)
    path.write_text(text)
    if echo:
        sys.stdout.write(text)
        sys.stdout.flush()
    return path
