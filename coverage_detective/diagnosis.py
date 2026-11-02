"""Why the signal is likely poor at a point: fixed rules over the evidence bundle, no model.

    from coverage_detective import evidence, diagnosis
    d = diagnosis.diagnose(evidence.build_bundle("CO9 1TP"))

Each cause from the fixed set (CAUSES) gets a rule score between 0 and 1 from the evidence items
that support or contradict it. A score is how strongly the public data points to the cause, not a
probability, and causes stay "likely": operators' network data is not public. Two caps apply:
a cause supported only by weak evidence (WEAK_CAP; see weak_reason) and every problem cause where
30+ tests measure at or above the UK median (MEASURES_WELL_CAP). Every number shown comes from
the evidence items the cause cites; this module writes no figures of its own.
"""

from __future__ import annotations

OPERATORS = ("EE", "O2", "Three", "Vodafone")
CAUSES = {
    "terrain_blocking": "Terrain between this point and the nearest mast sites",
    "distance_to_mast": "Distance to the nearest mast sites",
    "single_operator": "Few operators with mast sites nearby",
    "capacity_strain": "Busy-network capacity strain",
    "weather": "Severe weather",
    "active_incident": "An active incident nearby",
    "insufficient_data": "Not enough data",
}
MIN_TESTS = 30          # fewer Ookla tests than this count as weak evidence
WEAK_CAP = 0.5          # a cause supported only by weak evidence scores at most this
MEASURES_WELL_CAP = 0.3  # where the service measures well, no problem cause scores above this
TOP_CAUSES = 4
RESIDENT_ACTIONS = [
    "Check your address on Ofcom's mobile coverage checker: https://www.ofcom.org.uk/mobile-coverage-checker",
    "Emergency calls work on any available network.",
    "If you have home broadband, Wi-Fi calling can carry calls where the mobile signal is weak.",
]
METHOD = ("Causes are scored by fixed rules over the evidence (diagnosis.py), not by a language model. A score says "
          "how strongly the public data points to a cause; it is not a probability.")


# ------------------------------------------------------------------ reading the evidence


def _v(ev: dict, i: str) -> dict:
    return (ev.get(i) or {}).get("value") or {}


def _tests(ev: dict, i: str) -> int:
    return int(((ev.get(i) or {}).get("sample_size") or {}).get("tests") or 0)


def measured(ev: dict) -> bool:
    """At least 10 speed tests in the cell or within 1 km."""
    return any(_v(ev, i).get("download_mbps") is not None and _tests(ev, i) >= 10 for i in ("speed_cell", "speed_1km"))


def measures_well(ev: dict) -> bool:
    """30+ tests in the cell at or above the UK median."""
    v = _v(ev, "speed_cell")
    return (v.get("download_mbps") is not None and v.get("uk_median_download_mbps") is not None
            and _tests(ev, "speed_cell") >= MIN_TESTS and v["download_mbps"] >= v["uk_median_download_mbps"])


def weak_reason(item: dict) -> str | None:
    """Why an evidence item counts as weak, or None."""
    size, value = item.get("sample_size") or {}, item.get("value") or {}
    if item.get("strength") == "weak":
        return "labelled weak"
    if isinstance(size.get("tests"), (int, float)) and size["tests"] < MIN_TESTS:
        return f"{size['tests']:.0f} tests"
    if isinstance(value, dict) and "Met Office" in (item.get("source") or ""):
        ws = value.get("warnings") or []
        if ws and not any(w.get("district_listed") for w in ws):
            return "region-wide warning, not this district"
    if isinstance(value, dict) and "Approximate mast sites" in (item.get("source") or ""):
        if size.get("sites_unknown_operator"):
            return "sites with unknown operators"
        if not value.get("operators_confirmed"):
            return "crowd-sourced sites only"
    return None


def _ops(ids: list[str]) -> str:
    """los_ee_three, los_o2 -> "EE, Three and O2" (a shared site's id names each of its operators)."""
    names = {o.lower(): o for o in OPERATORS}
    out = [names.get(p, p) for i in ids for p in i.removeprefix("los_").split("_")]
    return " and ".join([", ".join(out[:-1]), out[-1]]) if len(out) > 1 else "".join(out)


# ------------------------------------------------------------------ the rules


def causes(ev: dict) -> list[dict]:
    """The scored causes, highest first (at most TOP_CAUSES)."""
    los = [i for i in ev if i.startswith("los_")]
    blocked = [i for i in los if _v(ev, i).get("blocked") or _v(ev, i).get("los_class") == "blocked"]
    clear = [i for i in los if _v(ev, i).get("los_class") == "clear"]
    out: list[dict] = []

    def add(cause, score, sup, con, text):
        weak = {i: weak_reason(ev[i]) for i in sup if i in ev}
        capped = bool(sup) and all(weak.values()) and score > WEAK_CAP
        out.append({"cause": cause, "label": CAUSES[cause],
                    "score": round(max(0.05, min(WEAK_CAP if capped else score, 0.95)), 2),
                    "evidence_ids": sup, "contradicting_ids": con, "explanation": text,
                    "weak_only": bool(sup) and all(weak.values())})

    if blocked:
        score = 0.7 if any(ev[i].get("strength") == "strong" for i in blocked) else 0.5
        if len(clear) >= len(blocked):
            score -= 0.2
        add("terrain_blocking", score, blocked, clear,
            f"Terrain rises above the straight line from this point to the nearest site of {_ops(blocked)}.")
    near = _v(ev, "nearest_site")
    if "nearest_site" in ev and near.get("distance_km") is None:
        sup = ["nearest_site", *[i for i in ev if i.startswith("no_site_")]]
        add("distance_to_mast", 0.35, sup, [], "No approximate mast sites were found nearby in the crowd-sourced data, "
            "which is weak evidence because that data is incomplete.")
    elif near.get("distance_km") is not None and near["distance_km"] >= 5:
        add("distance_to_mast", 0.5, ["nearest_site"], [], "The nearest approximate mast site is far from this point.")
    ops = _v(ev, "operators_5km").get("operators")
    if ops is not None and len(ops) <= 1 and near.get("distance_km") is not None:
        n = len(ops)
        unknown = int(((ev.get("operators_5km") or {}).get("sample_size") or {}).get("sites_unknown_operator") or 0)
        add("single_operator", 0.4, ["operators_5km"], [],
            ("No operator has" if n == 0 else "Only 1 operator has") + " approximate mast sites within 5 km"
            + (f"; operators could not be identified for {unknown} nearby site{'s' if unknown != 1 else ''}."
               if unknown else "."))
    if measured(ev) and not measures_well(ev):
        sup = [i for i in ("speed_trend", "usage_per_head") if i in ev and (
            (i == "speed_trend" and (_v(ev, i).get("change_pct") or 0) <= -20)
            or (i == "usage_per_head" and _v(ev, i).get("devices_per_1k_pop", 0) > _v(ev, i).get("uk_p50", 1e9)))]
        if sup:
            add("capacity_strain", 0.25 + 0.15 * len(sup) - (0.15 if blocked else 0), sup, blocked,
                "Speeds are low here, and falling speeds or heavy use per head point to a busy network.")
    if _v(ev, "weather").get("severity") == "severe":
        add("weather", 0.3, ["weather"], [], "Severe weather is forecast here; it can only explain short-lived problems.")
    if _v(ev, "flood_warnings").get("warnings"):
        add("active_incident", 0.2, ["flood_warnings"], [], "Flood alerts are in force nearby; they can only explain "
            "short-lived problems.")
    if not measured(ev):
        sup = [i for i in ("speed_cell", "speed_1km") if i in ev]
        if sup:
            add("insufficient_data", 0.5, sup, [], "Too few speed tests were recorded here to measure the service.")
    if measures_well(ev):
        for c in out:
            c["score"] = min(c["score"], MEASURES_WELL_CAP)
    return sorted(out, key=lambda c: -c["score"])[:TOP_CAUSES]


def ruled_out(ev: dict) -> list[dict]:
    out = []
    if "weather" in ev and _v(ev, "weather").get("severity") != "severe":
        out.append({"cause": "weather", "reason": "The forecast shows no severe weather here.", "evidence_ids": ["weather"]})
    if "flood_warnings" in ev and _v(ev, "flood_warnings") and not _v(ev, "flood_warnings").get("warnings"):
        out.append({"cause": "active_incident", "reason": "No flood alerts or warnings are in force nearby.",
                    "evidence_ids": ["flood_warnings"]})
    return out


def headline(ev: dict, ranked: list[dict]) -> str:
    top = next((c for c in ranked if c["score"] >= 0.4), None)
    if measures_well(ev):
        return "No strong cause was found; the service measures well here."
    if ranked and ranked[0]["cause"] == "insufficient_data" and not (_tests(ev, "speed_cell") or _tests(ev, "speed_1km")):
        why = ["there are no measured speeds at this point or within 1 km"]
        if "nearest_site" in ev and _v(ev, "nearest_site").get("distance_km") is None:
            why.append("no mast sites in our data within 10 km, so operators could not be identified")
        elif not _v(ev, "operators_5km").get("operators"):
            why.append("operators could not be identified for the sites nearby")
        return "Not enough data to say why: " + " and ".join(why) + "."
    if top and top["cause"] == "insufficient_data":
        return "There is not enough data to say why the signal is poor here."
    if top:
        return f"{top['label']} is the likely cause."
    return "No cause stands out from the evidence gathered here."


def operator_limits(ev: dict) -> list[str]:
    """A fixed line when no operator can be identified near the point."""
    v = _v(ev, "operators_5km")
    if "nearest_site" in ev and _v(ev, "nearest_site").get("distance_km") is None:
        where = "no approximate mast sites were found within 10 km in our data"
    elif v.get("operator_data_status") in ("no_sites_found", "unknown") and not v.get("operators"):
        where = "the approximate mast sites nearby have no known operator"
    else:
        return []
    return [f"Operators could not be identified: {where}. This is not the same as no coverage: crowd-sourced site "
            "data is incomplete; check the address on Ofcom's mobile coverage checker."]


def diagnose(bundle: dict) -> dict:
    """The rule-based diagnosis for an evidence bundle (evidence.build_bundle)."""
    ev = {i["id"]: i for i in bundle["items"]}
    ranked = causes(ev)
    best = bundle.get("likely_best") or {}
    return {
        "headline": headline(ev, ranked),
        "causes": ranked,
        "ruled_out": ruled_out(ev),
        "best_network": {"operators": best.get("operators") or [], "text": best.get("text"), "rule": best.get("rule")},
        "actions": RESIDENT_ACTIONS,
        "limitations": [METHOD, *operator_limits(ev), *bundle.get("limitations", [])],
        "method": METHOD,
    }
