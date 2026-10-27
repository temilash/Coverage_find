"""Rules only, no model: sample mode's stand-in for the model calls at a point with no recorded run.

    python run.py --sample, then a point inside the sample extracts that is not one of the examples

Everything else runs as usual: the coordinator's loop, the tools (DuckDB over the committed extracts in
place of Zetaris), line of sight, the guard and every Meterless write. Only the agents' replies come
from the fixed rules below, which read the evidence items of this run, so nothing is copied from
another place and no number is written that is not in the evidence. Results are labelled "rules only,
no model (sample mode)" (result["rules_only"]). Each reply is chosen by the shape asked for:

  investigator    no tool calls of its own: the coordinator's fixed round-1 checks (and the critic's) run
  diagnostician   causes ranked by the rules in `hypotheses` (the guard then caps weak-only beliefs)
  critic          asks once for the speed trend when speeds were measured and the trend was not run;
                  otherwise approves
  advisor         headline from the top belief, ruled-out causes, best network from network_comparison
  letter writer   a short letter from the headline and the reviewed beliefs
  guard rewrite   no rewrite (the guard then drops the sentence; the rules write no numbers)
"""

from __future__ import annotations

from coverage_detective.agents import schemas as S
from coverage_detective.agents.llm import LLMResult
from coverage_detective.agents.replay import ReplayMissing

LABEL = "rules only, no model (sample mode)"
CAUSE_PHRASE = {
    "terrain_blocking": "Terrain between this point and the nearest mast sites",
    "distance_to_mast": "Distance to the nearest mast sites",
    "single_operator": "Few operators with mast sites nearby",
    "capacity_strain": "Busy-network capacity strain",
    "weather": "Severe weather",
    "active_incident": "An active incident nearby",
    "insufficient_data": "Not enough data",
}


class Session:
    """Marks a run as rules only (case.cache["replay"]); the API and the coordinator read `mode`."""

    mode = "rules"
    example_id = None
    variant = None


def reply(case, agent: str, round: int, schema, tools: bool) -> LLMResult:
    ev = {e["evidence_id"]: e for e in case.memory.evidence(case.run_id)}
    if tools and schema is None:  # investigator: the coordinator adds the required checks
        return _result()
    if schema is S.HypothesisSet:
        return _result(diagnose(ev, case.memory.beliefs(case.run_id), round))
    if schema is S.CritiqueSet:
        return _result(critique(ev, [b for b in case.memory.beliefs(case.run_id) if b.get("status", "active") == "active"],
                                round))
    if schema is S.AdvisorOutput:
        return _result(advise(ev, case.memory.beliefs(case.run_id)))
    if schema is S.Letter:
        return _result(letter(ev, case.memory.beliefs(case.run_id), case.point))
    if schema is S.RewriteSet:
        return _result(S.RewriteSet(rewrites=[]))
    raise ReplayMissing(f"sample mode has no rules for the {agent} ({LABEL} covers point diagnoses and letters)")


def _result(parsed=None) -> LLMResult:
    return LLMResult(model="rules", parsed=parsed, provider="rules", model_answered="rules (no model)",
                     receipt={"note": LABEL})


# ------------------------------------------------------------------ reading the evidence


def _v(ev: dict, i: str) -> dict:
    return (ev.get(i) or {}).get("value") or {}


def _tests(ev: dict, i: str) -> int:
    return int(((ev.get(i) or {}).get("sample_size") or {}).get("tests") or 0)


def measured(ev: dict) -> bool:
    """At least 10 speed tests in the cell or within 1 km."""
    return any(_v(ev, i).get("download_mbps") is not None and _tests(ev, i) >= 10 for i in ("speed_cell", "speed_1km"))


def measures_well(ev: dict) -> bool:
    """30+ tests in the cell at or above the UK median (then no problem cause goes above 0.3)."""
    v = _v(ev, "speed_cell")
    return (v.get("download_mbps") is not None and v.get("uk_median_download_mbps") is not None
            and _tests(ev, "speed_cell") >= 30 and v["download_mbps"] >= v["uk_median_download_mbps"])


OPERATOR_NAMES = {"ee": "EE", "o2": "O2", "three": "Three", "vodafone": "Vodafone"}


def _ops(ids: list[str]) -> str:
    """los_ee, los_three -> "EE and Three"."""
    names = [OPERATOR_NAMES.get(i.split("_", 1)[1], i) for i in ids]
    return " and ".join([", ".join(names[:-1]), names[-1]]) if len(names) > 1 else "".join(names)


def _cite(ids: list[str]) -> str:
    return "".join(f"[{i}]" for i in ids)


# ------------------------------------------------------------------ the rules


def hypotheses(ev: dict) -> list[S.Hypothesis]:
    los = [i for i in ev if i.startswith("los_")]
    blocked = [i for i in los if _v(ev, i).get("blocked") or _v(ev, i).get("los_class") == "blocked"]
    clear = [i for i in los if _v(ev, i).get("los_class") == "clear"]
    out: list[S.Hypothesis] = []

    def add(cause, conf, sup, con, text):
        out.append(S.Hypothesis(id=cause, cause=cause, confidence=round(max(0.05, min(conf, 0.95)), 2),
                                supporting_evidence=sup, contradicting_evidence=con, explanation=f"Rules only: {text}"))

    if blocked:
        conf = 0.7 if any(ev[i].get("strength") == "strong" for i in blocked) else 0.5
        if len(clear) >= len(blocked):
            conf -= 0.2
        add("terrain_blocking", conf, blocked, clear,
            f"terrain rises above the line from this point to the nearest site of {_ops(blocked)} {_cite(blocked)}.")
    near = _v(ev, "nearest_site")
    if "nearest_site" in ev and near.get("distance_km") is None:
        sup = ["nearest_site", *[i for i in ev if i.startswith("no_site_")]]
        add("distance_to_mast", 0.35, sup, [], f"no approximate mast sites were found nearby in the crowd-sourced "
            f"data, which is weak evidence because that data is incomplete {_cite(sup)}.")
    elif near.get("distance_km") is not None and near["distance_km"] >= 5:
        add("distance_to_mast", 0.5, ["nearest_site"], [], "the nearest approximate mast site is far from this point "
            "[nearest_site].")
    ops = _v(ev, "operators_5km").get("operators")
    if ops is not None and len(ops) <= 1 and near.get("distance_km") is not None:
        add("single_operator", 0.4, ["operators_5km"], [], "few operators have approximate mast sites within reach "
            "[operators_5km].")
    if measured(ev) and not measures_well(ev):
        sup = [i for i in ("speed_trend", "usage_per_head") if i in ev and (
            (i == "speed_trend" and (_v(ev, i).get("change_pct") or 0) <= -20)
            or (i == "usage_per_head" and _v(ev, i).get("devices_per_1k_pop", 0) > _v(ev, i).get("uk_p50", 1e9)))]
        if sup:
            add("capacity_strain", 0.25 + 0.15 * len(sup) - (0.15 if blocked else 0), sup, blocked,
                f"speeds are low here and the usage or trend evidence points to a busy network {_cite(sup)}.")
    if _v(ev, "weather").get("severity") == "severe":
        add("weather", 0.3, ["weather"], [], "severe weather is forecast here [weather].")
    floods = _v(ev, "flood_warnings").get("warnings") or []
    if floods:
        add("active_incident", 0.2, ["flood_warnings"], [], "flood alerts are in force nearby [flood_warnings].")
    if not measured(ev):
        sup = [i for i in ("speed_cell", "speed_1km") if i in ev]
        if sup:
            add("insufficient_data", 0.5, sup, [], f"too few speed tests were recorded here to measure the service "
                f"{_cite(sup)}.")
    if measures_well(ev):
        out = [h.model_copy(update={"confidence": min(h.confidence, 0.3)}) for h in out]
    return sorted(out, key=lambda h: -h.confidence)[:4]


def diagnose(ev: dict, beliefs: list[dict], round: int) -> S.HypothesisSet:
    hs = hypotheses(ev)
    old = {b["belief_id"]: b["confidence"] for b in beliefs}
    reasons = [S.RevisionReason(id=h.cause, reason=f"rules re-run in round {round} with the new evidence")
               for h in hs if h.cause in old and abs(old[h.cause] - h.confidence) >= 0.005]
    return S.HypothesisSet(hypotheses=hs, revision_reasons=reasons)


def critique(ev: dict, beliefs: list[dict], round: int) -> S.CritiqueSet:
    out = []
    ask_trend = round == 1 and measured(ev) and "speed_trend" not in ev
    for n, b in enumerate(sorted(beliefs, key=lambda b: -b["confidence"])):
        if n == 0 and ask_trend:
            out.append(S.Critique(hypothesis_id=b["belief_id"], verdict="needs_evidence", suggested_confidence=None,
                                  reason="Rules only: speeds were measured but not their trend, which shows whether "
                                         "slow service is long-standing.",
                                  requested_checks=[S.RequestedCheck(tool="speed_trend", operator=None,
                                                                     why="is slow service long-standing?")]))
        else:
            out.append(S.Critique(hypothesis_id=b["belief_id"], verdict="approve", requested_checks=[],
                                  suggested_confidence=None,
                                  reason="Rules only: the belief cites its evidence " + _cite(b.get("supporting") or [])))
    return S.CritiqueSet(critiques=out)


def advise(ev: dict, beliefs: list[dict]) -> S.AdvisorOutput:
    active = sorted([b for b in beliefs if b.get("status", "active") == "active"], key=lambda b: -b["confidence"])
    top = next((b for b in active if b["confidence"] >= 0.4), None)
    if measures_well(ev):
        headline = "No strong cause was found; the service measures well here [speed_cell]."
    elif top and top["cause"] == "insufficient_data":
        headline = "There is not enough data to say why the signal is poor here [insufficient_data]."
    elif top:
        headline = f"{CAUSE_PHRASE[top['cause']]} is the likely cause [{top['belief_id']}]."
    else:
        headline = "No cause stands out from the evidence gathered here."
    ruled = []
    if "weather" in ev and _v(ev, "weather").get("severity") != "severe":
        ruled.append(S.RuledOut(cause="weather", reason="The forecast shows no severe weather here [weather].",
                                evidence=["weather"]))
    if "flood_warnings" in ev and _v(ev, "flood_warnings") and not _v(ev, "flood_warnings").get("warnings"):
        ruled.append(S.RuledOut(cause="active_incident", reason="No flood alerts or warnings are in force nearby "
                                "[flood_warnings].", evidence=["flood_warnings"]))
    best = _v(ev, "network_comparison").get("likely_best") or {}
    ranking = [S.NetworkRank(operator=op, rank=n + 1, confidence="low", evidence=["network_comparison"],
                             why="Rules only: the clearest line of sight to a nearby site [network_comparison].")
               for n, op in enumerate(best.get("operators") or []) if op in ("EE", "O2", "Three", "Vodafone")][:4]
    return S.AdvisorOutput(
        headline=headline,
        explanation=" ".join(b.get("explanation") or "" for b in active[:3]) or "The evidence gathered is listed below.",
        cited_evidence=(top or {}).get("supporting") or [],
        ruled_out=ruled,
        limitations=f"{LABEL[0].upper()}{LABEL[1:]}: causes were ranked by fixed rules from the evidence, without a "
                    "language model.",
        best_network=S.BestNetwork(summary=(ev.get("network_comparison") or {}).get("finding")
                                   or "No network can be ranked from the evidence.", ranking=ranking),
        actions=[])


def letter(ev: dict, beliefs: list[dict], point: dict) -> S.Letter:
    active = sorted([b for b in beliefs if b.get("status", "active") == "active"], key=lambda b: -b["confidence"])
    where = point.get("admin_district") or "my area"
    causes = "\n".join(f"- {b['cause'].replace('_', ' ')}: {b.get('explanation')}" for b in active[:3])
    cited = sorted({i for b in active[:3] for i in (b.get("supporting") or [])})
    body = (f"Dear Sir or Madam,\n\nI am writing about poor mobile signal at my location in {where}. A sample-mode "
            f"check (rules only, no language model) found these likely causes in public data:\n\n{causes}\n\n"
            "Please review the coverage here and tell me what improvements are planned.\n\nYours faithfully")
    return S.Letter(recipient="operator", subject=f"Poor mobile signal in {where}", body_markdown=body,
                    cited_evidence=cited)
