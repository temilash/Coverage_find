"""The agents. Each reads the case file from Meterless, calls Nemotron, passes the code guard and
writes its result back to Meterless. Agents never message each other.

  investigate()   Investigator    3.5 Super, tool calling, thinking off
  diagnose()      Diagnostician   3.5 Super, strict json_schema (DIAGNOSTICIAN_ULTRA_ROUNDS: rounds on Ultra)
  critique()      Critic          Ultra, thinking on, JSON schema in the prompt
  advise()        Advisor         3.5 Super, strict json_schema
  write_letter()  Letter writer   3.5 Super, strict json_schema
  plan_fix()      Fix planner     3.5 Super, strict json_schema (+ review_fix by the critic)

Every LLM call is recorded as an "llm_call" note (model, time, tokens, retries, model
switches); every guard intervention as a "guard" note.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from coverage_detective.agents import schemas as S
from coverage_detective.agents.guard import (CAP_WEAK_ONLY, CONTEXT_ONLY, Guard, Intervention, cited_in_text,
                                             map_cause_citations)
from coverage_detective.agents.llm import LLMError, LLMResult, llm
from coverage_detective.agents.replay import ReplayMissing
from coverage_detective.agents.tools import (OPERATORS, POINT_TOOLS, ZONE_TOOLS, Case, network_comparison, run_tool,
                                             tool_specs)

PROMPTS = Path(__file__).parent / "prompts"
MAX_CALLS_PER_ROUND = 8
DIAGNOSTICIAN_ULTRA_ROUNDS: set[int] = set()  # rounds where the diagnostician uses Ultra with thinking
ROUND1_POINT = [("speeds_near", {}), ("sites_near", {}), *[("line_of_sight", {"operator": o}) for o in OPERATORS],
                ("ofcom_area", {}), ("live_conditions", {})]
ROUND1_ZONE = [("zone_summary", {}), ("candidate_sites", {}), ("prior_diagnoses_nearby", {})]


def prompt(name: str, **fmt) -> str:
    text = (PROMPTS / f"{name}.md").read_text()
    return text.format(**fmt) if fmt else text


# ------------------------------------------------------------------ shared helpers


def ask(case: Case, agent: str, round: int, messages: list[dict], purpose: str | None = None, **kw) -> LLMResult:
    """One LLM call, recorded in Meterless (success or failure). purpose: e.g. "guard rewrite"."""
    t0 = time.monotonic()
    label = f"{agent} {purpose}" if purpose else f"{agent} thinking" if kw.get("reasoning") else agent
    step = case.progress.start(agent, round, "llm", label) if case.progress else None
    sess = case.cache.get("replay")  # sample mode: replay recorded replies, or record them (agents/replay.py)
    try:
        if sess and sess.mode == "replay":
            r = sess.reply(sess.next_key(agent, round), kw.get("schema"))
        else:
            r = llm.chat(messages, **kw)
            if sess:
                sess.save(sess.next_key(agent, round), r)
    except ReplayMissing as e:
        case.memory.add_note(case.run_id, "replay_missing", str(e), by="coordinator", round=round, agent=agent)
        if step:
            case.progress.fail(step, str(e)[:160])
        raise
    except LLMError as e:
        case.memory.add_note(case.run_id, "llm_error", f"{agent}: {str(e)[:200]}", by=agent, round=round,
                             retries=e.retries, duration_ms=round_ms(t0))
        if step:
            case.progress.fail(step, str(e)[:160])
        raise
    if step:
        case.progress.done(step, f"{r.model.split('/')[-1]}, {r.duration_ms / 1000:.1f}s"
                           + (f", {len(r.retries)} retries" if r.retries else "") + (", model switched" if r.fallback else ""))
    case.memory.add_note(case.run_id, "llm_call", f"{agent} via {r.model.split('/')[-1]} in {r.duration_ms / 1000:.1f}s"
                         + (f"; {len(r.retries)} retr{'y' if len(r.retries) == 1 else 'ies'}" if r.retries else "")
                         + (f"; {r.fallback}" if r.fallback else ""),
                         by=agent, round=round, model=r.model, duration_ms=r.duration_ms, tokens=r.usage,
                         attempts=r.attempts, retries=r.retries, fallback=r.fallback,
                         thinking=bool(kw.get("reasoning")), purpose=purpose)
    return r


def round_ms(t0: float) -> int:
    return round((time.monotonic() - t0) * 1000)


def evidence_map(case: Case) -> dict[str, dict]:
    return {e["evidence_id"]: e for e in case.memory.evidence(case.run_id)}


def evidence_text(ev: dict[str, dict]) -> str:
    return "\n".join(f"[{i}] ({e.get('strength')}; {e.get('source')}; {e.get('data_period')}) {e.get('finding')}"
                     for i, e in ev.items())


def beliefs_text(beliefs: list[dict]) -> str:
    if not beliefs:
        return "(none yet)"
    out = []
    for b in beliefs:
        hist = " -> ".join(f"{h['confidence']:.2f}" for h in b.get("history", []) if h.get("confidence") is not None)
        out.append(f"- {b['belief_id']} ({b.get('status', 'active')}) confidence {b['confidence']:.2f} [{hist}]; "
                   f"for {b.get('supporting')}; against {b.get('contradicting')}: {b.get('explanation')}")
    return "\n".join(out)


def already_run(case: Case) -> list[tuple[str, str | None]]:
    seen = []
    for n in case.memory.notes(case.run_id):
        if n.get("type") == "tool_call":
            seen.append((n["tool"], (n.get("args") or {}).get("operator")))
    return list(dict.fromkeys(seen))


def _fmt_run(runs: list[tuple[str, str | None]]) -> str:
    return ", ".join(f"{t}({o})" if o else f"{t}()" for t, o in runs) or "(nothing yet)"


def make_guard(case: Case, agent: str, round: int, beliefs: list[dict] | None = None) -> Guard:
    """beliefs: given for the advisor, which may cite the reviewed beliefs by id."""
    def record(iv: Intervention) -> None:
        case.memory.add_note(case.run_id, "guard", iv.summary, by="guard", round=round, agent=agent,
                             guard_kind=iv.kind, **iv.details)
    return Guard(evidence_map(case), record, {b["belief_id"]: b for b in beliefs or []})


def zone_type_line(case: Case) -> str:
    """For zone runs: which kind of zone this is (the agents' wording depends on it)."""
    if case.scope != "zone":
        return ""
    kind = ((case.cache.get("zone") or {}).get("zone") or {}).get("zone_type", "gap")
    return ("Zone type: improvement opportunity (cells that are not poorly served but have a measurable weakness; "
            "call it an opportunity, never a gap, and never say people have no service or no coverage).\n"
            if kind == "opportunity" else "Zone type: gap zone (poorly served cells).\n")


def advisor_beliefs_text(beliefs: list[dict]) -> str:
    """The reviewed beliefs for the advisor, each with the id to cite and its cause name."""
    if not beliefs:
        return "(none)"
    return "\n".join(f"- [{b['belief_id']}] cause {b.get('cause') or b['belief_id']}, {b.get('status', 'active')}, confidence "
                     f"{b['confidence']:.2f}; for {b.get('supporting')}: {b.get('explanation')}" for b in beliefs)


def rewriter(case: Case, agent: str, round: int, system: str, reasoning: bool = False):
    """The guard's one rewrite request goes back to the same agent (its prompt), on the fast model."""
    def rewrite(sentences: list[str], cited: list[str]) -> dict[str, str]:
        ev = evidence_map(case)
        msg = ("These sentences contain numbers that are not in the evidence they cite. Rewrite each one so it only "
               "uses numbers exactly as written in the evidence below (or no numbers), citing ids in [brackets].\n\n"
               "Sentences:\n" + "\n".join(f"- {s}" for s in sentences) + "\n\nEvidence:\n"
               + evidence_text({i: ev[i] for i in cited if i in ev}))
        # Same agent and prompt; the fast model without thinking (rewriting one sentence needs no reasoning,
        # and on Ultra a rewrite took up to 82 s).
        r = ask(case, agent, round, [{"role": "system", "content": system}, {"role": "user", "content": msg}],
                purpose="guard rewrite", schema=S.RewriteSet)
        return {w.original: w.rewritten for w in r.parsed.rewrites}
    return rewrite


# ------------------------------------------------------------------ investigator


def investigate(case: Case, round: int, requested: list[dict] | None = None, question: str = "Why is mobile signal poor here?") -> list[str]:
    """Choose and run tools. Returns the evidence ids produced this round."""
    zone = case.scope == "zone"
    names = ZONE_TOOLS if zone else POINT_TOOLS
    done = already_run(case)
    requested = requested or []
    prior = evidence_map(case).get("prior_diagnoses")
    user = (f"Question: {question}\nScope: {'gap zone ' + case.zone_id if zone else 'point ' + case.point.get('label', '')}\n"
            f"Round: {round}\nCurrent beliefs:\n{beliefs_text(case.memory.beliefs(case.run_id))}\n"
            f"Prior diagnoses nearby: {prior['finding'] if prior else 'not checked yet'}\n"
            f"Critic's requested checks: {json.dumps(requested) if requested else 'none'}\n"
            f"Already run: {_fmt_run(done)}")
    calls: list[tuple[str, dict]] = []
    try:
        r = ask(case, "investigator", round, [{"role": "system", "content": prompt("investigator")},
                                             {"role": "user", "content": user}], tools=tool_specs(names))
        calls = [(c["name"], {k: v for k, v in c["arguments"].items() if k == "operator"}) for c in r.tool_calls
                 if c["name"] in names]
    except LLMError:
        case.memory.add_note(case.run_id, "fallback", "investigator unavailable: running the fixed tool plan in code",
                             by="coordinator", round=round)
    # The fixed minimum: round 1's standard checks, and the critic's requests in later rounds.
    required = (ROUND1_ZONE if zone else ROUND1_POINT) if round == 1 else \
        [(c["tool"], {"operator": c["operator"]} if c.get("operator") else {}) for c in requested]
    missing = [c for c in required if (c[0], c[1].get("operator")) not in {(n, a.get("operator")) for n, a in calls}]
    if missing:
        case.memory.add_note(case.run_id, "coordinator", f"added required checks the investigator skipped: "
                             f"{_fmt_run([(n, a.get('operator')) for n, a in missing])}", by="coordinator", round=round)
        calls += missing
    # Required checks first, then the investigator's extras, at most 8 calls a round.
    req_keys = {(n, a.get("operator")) for n, a in required}
    calls = [c for c in calls if (c[0], c[1].get("operator")) in req_keys] + \
        [c for c in calls if (c[0], c[1].get("operator")) not in req_keys]
    plan, seen = [], set(done)
    for name, args in calls:
        key = (name, args.get("operator"))
        if key in seen or (name == "line_of_sight" and args.get("operator") not in OPERATORS):
            continue
        seen.add(key)
        plan.append((name, args))
    produced = []
    if len(plan) > MAX_CALLS_PER_ROUND:
        case.memory.add_note(case.run_id, "coordinator", f"capped at {MAX_CALLS_PER_ROUND} calls; skipped "
                             f"{_fmt_run([(n, a.get('operator')) for n, a in plan[MAX_CALLS_PER_ROUND:]])}",
                             by="coordinator", round=round)
    for name, args in plan[:MAX_CALLS_PER_ROUND]:
        try:
            produced += [i["id"] for i in run_tool(case, name, args, round=round)]
        except Exception:  # noqa: BLE001 - recorded as a tool_error note by run_tool
            continue
    if not zone and round == 1:
        produced += [i["id"] for i in _record(case, network_comparison(case), round)]
    return produced


def _record(case: Case, items: list[dict], round: int) -> list[dict]:
    for item in items:
        case.memory.add_evidence(case.run_id, {**item, "evidence_id": item["id"], "tool": "network_comparison",
                                               "round": round, "zetaris": case.sql_for(item["query_or_url"])}, by="coordinator")
    return items


# ------------------------------------------------------------------ diagnostician


def diagnose(case: Case, round: int) -> list[dict]:
    """Write or revise up to 4 ranked beliefs in Meterless."""
    ev = evidence_map(case)
    beliefs = case.memory.beliefs(case.run_id)
    crit = [c for c in case.memory.critiques(case.run_id) if c.get("round") == round - 1]
    system = prompt("diagnostician")
    user = (f"Round {round}. Scope: {case.scope}.\n{zone_type_line(case)}\nEvidence:\n{evidence_text(ev)}\n\nCurrent beliefs:\n{beliefs_text(beliefs)}\n\n"
            "Critic's last review:\n" + ("\n".join(f"- {c.get('belief_id') or c.get('target')}: {c['verdict']}: {c['reason']}"
                                                    for c in crit) or "(none)"))
    r = ask(case, "diagnostician", round, [{"role": "system", "content": system}, {"role": "user", "content": user}],
            reasoning=round in DIAGNOSTICIAN_ULTRA_ROUNDS, schema=S.HypothesisSet)
    guard = make_guard(case, "diagnostician", round)
    reasons = {x.id: x.reason for x in r.parsed.revision_reasons}
    existing = {b["belief_id"]: b for b in beliefs}
    written, seen = [], set()
    for h in sorted(r.parsed.hypotheses, key=lambda h: -h.confidence):
        bid = h.cause  # one belief per cause
        if bid in seen:
            continue
        seen.add(bid)
        sup = guard.context_only(guard.known(h.supporting_evidence, f"belief {bid} (supporting)"), f"belief {bid} (supporting)")
        con = guard.context_only(guard.known(h.contradicting_evidence, f"belief {bid} (contradicting)"),
                                 f"belief {bid} (contradicting)")
        if not sup and bid != "insufficient_data":
            guard.record(Intervention("dropped_belief", f"dropped belief {bid}: none of its supporting evidence is in Meterless",
                                      {"belief": bid}))
            continue
        conf = h.confidence
        weak = guard.weak_only(sup)
        if weak and conf > CAP_WEAK_ONLY:
            guard.record(Intervention("weak_cap", f"capped {bid} at {CAP_WEAK_ONLY}: all its evidence is weak ("
                                      + "; ".join(f"{i}: {w}" for i, w in weak.items()) + ")",
                                      {"belief": bid, "from": conf, "to": CAP_WEAK_ONLY, "weak": weak}))
            conf = CAP_WEAK_ONLY
        text = guard.strip_unknown_brackets(h.explanation, f"belief {bid}")
        text = guard.check_numbers({"explanation": text}, set(sup) | set(con) | cited_in_text(text), f"belief {bid}",
                                   rewriter(case, "diagnostician", round, system, True))["explanation"]
        if bid in existing:
            old = existing[bid]
            reason = reasons.get(bid) or reasons.get(h.id)
            if abs(old["confidence"] - conf) >= 0.005 and not reason:  # a change needs a stated reason
                guard.record(Intervention("unexplained_change", f"kept {bid} at {old['confidence']:.2f}: the diagnostician "
                                          f"moved it to {conf:.2f} without a reason", {"belief": bid, "from": old["confidence"],
                                                                                        "proposed": conf}))
                conf = old["confidence"]
            if abs(old["confidence"] - conf) >= 0.005 or old.get("supporting") != sup:
                case.memory.update_belief(case.run_id, bid, conf, reason or f"evidence updated in round {round}",
                                          by="diagnostician", round=round,
                                          explanation=text, supporting=sup, contradicting=con, status="active")
        else:
            case.memory.add_belief(case.run_id, bid, conf, text, sup, con, round=round)
        written.append(bid)
    return case.memory.beliefs(case.run_id)


# ------------------------------------------------------------------ critic


def critique(case: Case, round: int, final_round: bool) -> list[dict]:
    """Review every active belief; apply downgrades and rejections; return the requested checks."""
    # The critic judges the evidence of this run only: earlier diagnoses (context) are not shown to it,
    # since in Phase 4 and again in testing it treated them as support or as proof of a time pattern.
    ev = {i: e for i, e in evidence_map(case).items() if i not in CONTEXT_ONLY}
    beliefs = [b for b in case.memory.beliefs(case.run_id) if b.get("status", "active") == "active"]
    done = already_run(case)
    names = ZONE_TOOLS if case.scope == "zone" else POINT_TOOLS
    system = prompt("critic")
    user = (f"Round {round}{' (final round: only approve, downgrade or reject)' if final_round else ''}.\n"
            f"{zone_type_line(case)}\n"
            f"Evidence:\n{evidence_text(ev)}\n\nBeliefs:\n{beliefs_text(beliefs)}\n\n"
            f"Tools available: {', '.join(names)}\nAlready run: {_fmt_run(done)}")
    r = ask(case, "critic", round, [{"role": "system", "content": system}, {"role": "user", "content": user}],
            reasoning=True, schema=S.CritiqueSet)
    guard = make_guard(case, "critic", round)
    by_id = {b["belief_id"]: b for b in beliefs}
    requested: list[dict] = []
    seen_checks = set(done)
    for c in r.parsed.critiques:
        b = by_id.get(c.hypothesis_id)
        if b is None:
            guard.record(Intervention("unknown_citation", f"critique of unknown belief {c.hypothesis_id} ignored",
                                      {"belief": c.hypothesis_id}))
            continue
        reason = guard.strip_unknown_brackets(c.reason, f"critique of {b['belief_id']}")
        cited = cited_in_text(reason) | set(b.get("supporting") or []) | set(b.get("contradicting") or [])
        reason = guard.check_numbers({"reason": reason}, cited, f"critique of {b['belief_id']}",
                                     rewriter(case, "critic", round, system, True))["reason"]
        verdict, checks = c.verdict, []
        for chk in c.requested_checks:
            key = (chk.tool, chk.operator if chk.tool == "line_of_sight" else None)
            if chk.tool not in names or key in seen_checks:
                continue
            seen_checks.add(key)
            checks.append({"tool": chk.tool, "operator": key[1], "why": chk.why})
        if verdict == "needs_evidence" and (final_round or not checks):
            why = "final round" if final_round else "every requested check has already run"
            guard.record(Intervention("verdict_changed", f"critic asked for evidence on {b['belief_id']} but {why}: "
                                      "treated as a downgrade", {"belief": b["belief_id"]}))
            verdict, checks = "downgrade", []
        suggested = c.suggested_confidence
        if verdict == "downgrade":
            suggested = min(suggested if suggested is not None else b["confidence"] - 0.1, b["confidence"] - 0.05)
            suggested = max(0.05, round_(suggested))
            case.memory.update_belief(case.run_id, b["belief_id"], suggested, f"critic: {reason}", by="critic", round=round)
        elif verdict == "reject":
            suggested = min(b["confidence"], 0.1)
            case.memory.update_belief(case.run_id, b["belief_id"], suggested, f"critic rejected: {reason}", by="critic",
                                      round=round, status="rejected")
        case.memory.add_critique(case.run_id, {"belief_id": b["belief_id"], "verdict": verdict, "reason": reason,
                                               "requested_checks": checks, "suggested_confidence": suggested}, round=round)
        requested += checks
    return requested


def round_(x: float) -> float:
    return float(f"{x:.2f}")


# ------------------------------------------------------------------ advisor (point)


MANDATORY_ACTIONS = [
    "Check your address on Ofcom's mobile coverage checker: https://www.ofcom.org.uk/mobile-coverage-checker",
    "Emergency calls work on any available network.",
]


def advise(case: Case, audience: str = "resident") -> S.AdvisorOutput:
    ev = evidence_map(case)
    beliefs = case.memory.beliefs(case.run_id)
    system = prompt("advisor", audience=audience)
    user = (f"Location: {case.point.get('label')} ({case.point.get('admin_district')}, {case.point.get('country')}).\n\n"
            f"Reviewed beliefs (cite a belief by its id in [brackets], evidence by its id):\n{advisor_beliefs_text(beliefs)}"
            f"\n\nEvidence:\n{evidence_text(ev)}")
    r = ask(case, "advisor", 0, [{"role": "system", "content": system}, {"role": "user", "content": user}],
            schema=S.AdvisorOutput)
    out = r.parsed
    guard = make_guard(case, "advisor", 0, beliefs)
    bmap = {b["belief_id"]: b for b in beliefs}
    out.cited_evidence = map_cause_citations(out.cited_evidence, {}, bmap)[0]
    out.cited_evidence = guard.known(out.cited_evidence, "advisor")
    for ro in out.ruled_out:
        ro.evidence = guard.known(map_cause_citations(ro.evidence, {}, bmap)[0], f"advisor ruled_out {ro.cause}")
    for n in out.best_network.ranking:
        n.evidence = guard.known(n.evidence, f"advisor best_network {n.operator}")
    fields = {"headline": out.headline, "explanation": out.explanation, "limitations": out.limitations,
              "best_network.summary": out.best_network.summary,
              **{f"ruled_out.{i}": x.reason for i, x in enumerate(out.ruled_out)},
              **{f"best_network.{i}": x.why for i, x in enumerate(out.best_network.ranking)},
              **{f"actions.{i}": x.text for i, x in enumerate(out.actions)}}
    fields = map_cause_citations([], fields, bmap)[1]
    fields = {k: guard.strip_unknown_brackets(v, f"advisor {k}") for k, v in fields.items()}
    cited = set(out.cited_evidence) | {i for v in fields.values() for i in cited_in_text(v)} \
        | {i for x in out.ruled_out for i in x.evidence} | {i for x in out.best_network.ranking for i in x.evidence}
    if "network_comparison" in ev:
        cited.add("network_comparison")
    fields = guard.check_numbers(fields, cited, "advisor", rewriter(case, "advisor", 0, system, False))
    out.headline, out.explanation, out.limitations = fields["headline"], fields["explanation"], fields["limitations"]
    out.best_network.summary = fields["best_network.summary"]
    for i, x in enumerate(out.ruled_out):
        x.reason = fields[f"ruled_out.{i}"]
    for i, x in enumerate(out.best_network.ranking):
        x.why = fields[f"best_network.{i}"]
    for i, x in enumerate(out.actions):
        x.text = fields[f"actions.{i}"]
    out.actions = [a for a in out.actions if a.text]
    ranking, seen = [], set()  # one entry per operator, ranks renumbered
    for n in sorted(out.best_network.ranking, key=lambda n: n.rank):
        if n.operator not in seen:
            seen.add(n.operator)
            ranking.append(n.model_copy(update={"rank": len(ranking) + 1}))
    out.best_network.ranking = ranking
    lower = [a.text.lower() for a in out.actions]
    if not any("coverage checker" in t or "mobile-coverage-checker" in t for t in lower):
        out.actions.append(S.Action(text=MANDATORY_ACTIONS[0], audience="resident"))
    if not any("emergency" in t for t in lower):
        out.actions.append(S.Action(text=MANDATORY_ACTIONS[1], audience="resident"))
    return out


# ------------------------------------------------------------------ letter writer


def write_letter(case: Case, recipient: str, diagnosis: dict, addressee: str | None = None) -> S.Letter:
    """addressee: who the letter is to, by name ("Vodafone", "Cornwall Council"); defaults to the kind."""
    ev = evidence_map(case)
    cited = set(diagnosis.get("cited_evidence") or []) | {i for h in diagnosis.get("hypotheses", [])
                                                         for i in (h.get("supporting") or [])}
    system = prompt("letter_writer", recipient=addressee or {"operator": "the mobile operator",
                                                             "council": "the local council", "mp": "the local MP"}[recipient])
    user = (f"Location: {diagnosis['location'].get('label')}.\nHeadline: {diagnosis['headline']}\n"
            f"Explanation: {diagnosis.get('explanation')}\nLikely causes:\n"
            + "\n".join(f"- {h['cause']} confidence {h['confidence']:.2f}" for h in diagnosis.get("hypotheses", [])
                        if h.get("status", "active") == "active")
            + "\n\nEvidence:\n" + evidence_text({i: ev[i] for i in cited if i in ev}))
    r = ask(case, "letter_writer", 0, [{"role": "system", "content": system}, {"role": "user", "content": user}],
            schema=S.Letter)
    letter = r.parsed
    letter.recipient = recipient
    guard = make_guard(case, "letter_writer", 0)
    letter.cited_evidence = guard.known(letter.cited_evidence, "letter")
    body = guard.strip_unknown_brackets(letter.body_markdown, "letter")
    letter.body_markdown = guard.check_numbers({"body": body}, cited | set(letter.cited_evidence) | cited_in_text(body),
                                               "letter", rewriter(case, "letter_writer", 0, system, False))["body"]
    # Sources are listed by code from the evidence, so they are never missing or invented.
    used = [i for i in dict.fromkeys([*letter.cited_evidence, *sorted(cited_in_text(letter.body_markdown))]) if i in ev]
    if used:
        letter.body_markdown += "\n\n---\n**Evidence and sources**\n\n" + "\n".join(
            f"- [{i}] {ev[i]['finding']} ({ev[i].get('source')}; data {ev[i].get('data_period')}, as of "
            f"{str(ev[i].get('as_of') or '')[:10]})" for i in used)
    return letter


# ------------------------------------------------------------------ zone: fix planner, fix review, advice


def plan_fix(case: Case, round: int, review: S.FixReview | None = None) -> dict:
    ev = evidence_map(case)
    system = prompt("fix_planner")
    user = (f"Zone {case.zone_id}.\n{zone_type_line(case)}Beliefs:\n{beliefs_text(case.memory.beliefs(case.run_id))}\n\nEvidence:\n{evidence_text(ev)}"
            + (f"\n\nThe critic asked you to revise: {review.reason} Issues: {'; '.join(review.issues)}" if review else ""))
    r = ask(case, "fix_planner", round, [{"role": "system", "content": system}, {"role": "user", "content": user}],
            schema=S.FixPlan)
    plan = r.parsed
    guard = make_guard(case, "fix_planner", round)
    plan.cited_evidence = guard.known(plan.cited_evidence, "fix plan")
    if plan.candidate and plan.candidate not in ev:
        guard.known([plan.candidate], "fix plan candidate")
        plan.candidate = None
    elif plan.candidate and not re.match(r"^(cand|existing)_\d+$", plan.candidate):  # e.g. [fix_rule]: not a site
        guard.record(Intervention("not_a_candidate", f"fix plan: {plan.candidate} is not a candidate site; candidate cleared",
                                  {"candidate": plan.candidate}))
        plan.candidate = None
    why = guard.strip_unknown_brackets(plan.why, "fix plan")
    plan.why = guard.check_numbers({"why": why}, set(plan.cited_evidence) | cited_in_text(why) | {plan.candidate or ""},
                                   "fix plan", rewriter(case, "fix_planner", round, system, False))["why"]
    out = plan.model_dump()
    if plan.candidate:  # numbers come from the candidate's evidence, not the model
        out["candidate_values"] = ev[plan.candidate].get("value")
        out["candidate_finding"] = ev[plan.candidate].get("finding")
    return out


def review_fix(case: Case, round: int, plan: dict) -> S.FixReview:
    ev = evidence_map(case)
    user = f"{zone_type_line(case)}Fix plan:\n{json.dumps(plan, indent=1)}\n\nEvidence:\n{evidence_text(ev)}"
    r = ask(case, "critic", round, [{"role": "system", "content": prompt("fix_critic")}, {"role": "user", "content": user}],
            reasoning=True, schema=S.FixReview)
    case.memory.add_critique(case.run_id, {"belief_id": None, "target": "fix_plan", "verdict": r.parsed.verdict,
                                           "reason": r.parsed.reason, "issues": r.parsed.issues, "requested_checks": []},
                             round=round)
    return r.parsed


def advise_zone(case: Case) -> S.ZoneAdvice:
    ev = evidence_map(case)
    beliefs = case.memory.beliefs(case.run_id)
    system = prompt("advisor", audience="council officer") + "\nFor a zone: headline and explanation only."
    user = (f"Zone {case.zone_id}.\n{zone_type_line(case)}Reviewed beliefs (cite a belief by its id in [brackets], evidence by its id):\n"
            f"{advisor_beliefs_text(beliefs)}\n\nEvidence:\n{evidence_text(ev)}")
    r = ask(case, "advisor", 0, [{"role": "system", "content": system}, {"role": "user", "content": user}],
            schema=S.ZoneAdvice)
    out = r.parsed
    guard = make_guard(case, "advisor", 0, beliefs)
    keys = ("headline", "explanation", "limitations")
    out.cited_evidence, mapped = map_cause_citations(out.cited_evidence, {k: getattr(out, k) for k in keys},
                                                     {b["belief_id"]: b for b in beliefs})
    out.cited_evidence = guard.known(out.cited_evidence, "zone advice")
    fields = {k: guard.strip_unknown_brackets(mapped[k], f"zone advice {k}") for k in keys}
    fields = guard.check_numbers(fields, set(out.cited_evidence) | {i for v in fields.values() for i in cited_in_text(v)},
                                 "zone advice", rewriter(case, "advisor", 0, system, False))
    for k, v in fields.items():
        setattr(out, k, v)
    return out


# ------------------------------------------------------------------ council: priority summary


SUMMARY_KINDS = {
    "gap": {"kind": "mobile coverage gaps", "reasons": "slow measured speeds, no identified site within 5 km, or terrain "
            "blocking every site", "extra_rule": "",
            "example": "1. Ousby [Z00072]: 836 of 873 people have no identified site within 5 km; low data confidence (0 tests)."},
    "opportunity": {"kind": "mobile improvement opportunities (areas that are not poorly served but have a measurable "
                    "weakness)", "reasons": "speed well below the area or UK median, falling speeds, only 1-2 operators "
                    "identified, or a busy-network signal",
                    "extra_rule": " Call them opportunities, never gaps, and never say people have no service or no coverage.",
                    "example": "1. Townsend [O00042]: 420 of 1,300 people are in cells where speeds fell 30% or more; an "
                               "opportunity for a capacity upgrade; limited data (4 tests)."},
}
OPP_REASON_WORDS = {"slow": "in cells measured below half the reference median", "falling": "in cells whose speeds fell 30% or more",
                    "few_operators": "in cells with only 1-2 operators identified", "capacity": "in busy cells (capacity signal)"}


def zone_item(z: dict, diagnosis: dict | None = None) -> dict:
    """A ranked zone as an evidence item (id = zone id), so the guard can check the summary against it."""
    r = z["people_by_reason"]
    if z.get("zone_type") == "opportunity":
        finding = (f"{z['name']} ({z['council']}) is improvement opportunity {z['rank']} with {z['people']} people in "
                   f"{z['cells']} cells: " + ", ".join(f"{r[k]} {OPP_REASON_WORDS[k]}" for k in z["reasons"]) + ". "
                   f"{z['tests']} tests ({'limited data' if z.get('limited_data') else z['confidence'] + ' data confidence'}); "
                   f"operators with sites nearby: {', '.join(z['operators_nearby']) or 'none identified'}.")
        source, ref = "Improvement opportunities (Zetaris CD_OPP_ZONES)", "zetaris:CD_OPP_ZONES"
    else:
        finding = (f"{z['name']} ({z['council']}) is ranked {z['rank']} with {z['people']} people in {z['cells']} cells: "
                   f"{r['slow']} where measured speeds are under 5 Mbps, {r['no_site']} with no identified site within 5 km, "
                   f"{r['blocked']} where terrain blocks every site. {z['tests']} tests ({z['confidence']} data confidence); "
                   f"operators with sites nearby: {', '.join(z['operators_nearby']) or 'none identified'}.")
        source, ref = "Gap zones (Zetaris CD_GAP_ZONES)", "zetaris:CD_GAP_ZONES"
    if diagnosis:
        finding += f" Agent diagnosis ({str(diagnosis.get('created_at') or '')[:10]}): {diagnosis.get('headline')}"
    return {"id": z["zone_id"], "source": source, "data_period": z.get("data_period"),
            "as_of": z.get("as_of"), "query_or_url": ref, "cell_ids": z.get("cell_ids") or [],
            "finding": finding, "value": {k: z.get(k) for k in ("rank", "people", "cells", "tests", "score")} | r,
            "sample_size": {"tests": z["tests"]}, "strength": "moderate", "bears_on": []}


def write_priority_summary(case: Case, scope_name: str, zones: list[dict], diagnoses: dict[str, dict],
                           kind: str = "gap") -> S.PrioritySummary:
    items = [zone_item(z, diagnoses.get(z["zone_id"])) for z in zones]
    for it in items:
        case.memory.add_evidence(case.run_id, {**it, "evidence_id": it["id"], "round": 0}, by="coordinator")
    ev = {i: e for i, e in evidence_map(case).items() if i in {it["id"] for it in items}}
    system = prompt("priority_summary", scope=scope_name, **SUMMARY_KINDS[kind])
    r = ask(case, "advisor", 0, [{"role": "system", "content": system},
                                 {"role": "user", "content": f"Ranked zones in {scope_name}:\n{evidence_text(ev)}"}],
            schema=S.PrioritySummary)
    out = r.parsed
    guard = make_guard(case, "advisor", 0)
    out.cited_evidence = guard.known(out.cited_evidence, "priority summary")
    body = guard.strip_unknown_brackets(out.body_markdown, "priority summary")
    out.body_markdown = guard.check_numbers({"body": body}, set(ev), "priority summary",
                                            rewriter(case, "advisor", 0, system))["body"]
    return out
