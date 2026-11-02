"""Priority score for gap zones and opportunity score for improvement opportunities, computed per
request so the weights can change.

Each factor is normalised 0-1 within the chosen scope (UK, a nation or a council area) by
rank: the lowest raw value in the scope scores 0, the highest 1 (ties share a rank). Rank
normalisation keeps one huge zone from flattening every other zone's people score.

  factor           raw value (higher = higher priority)
  people           population of the zone
  severity         population-weighted cell severity (no site 1, blocked 0.75, slow by Mbps)
  persistence      share of the last 4 quarters below 5 Mbps (no tests: neutral 0.5, flagged)
  trend            -(change in download %), so worsening scores higher (no data: neutral 0.5)
  operator_choice  4 - mean operators with sites within 5 km
  need             rural-urban + deprivation: only when built (not yet), otherwise left out

Improvement opportunities use their own factors (kind="opportunity"):
  people           population of the zone
  shortfall        population-weighted % by which measured cells fall below their reference median
                   (the lower of the area and UK medians); no measurements: neutral 0.5, flagged
  trend            -(change in download %), so worsening scores higher
  operator_choice  4 - mean operators with sites within 5 km

score = 100 * sum(weight * normalised) / sum(weights of the factors used). Each factor's
contribution is reported. Data confidence (Ookla tests) is shown beside the score, not in it.
"""

from __future__ import annotations

import pandas as pd

from coverage_detective import config

FACTORS = ["people", "severity", "persistence", "trend", "operator_choice", "need"]
OPP_FACTORS = ["people", "shortfall", "trend", "operator_choice"]
NUMERIC = {"population", "severity", "persistence", "trend_pct", "operators_mean", "shortfall_pct", "need"}
NEUTRAL = 0.5


def factors(kind: str = "gap") -> list[str]:
    return OPP_FACTORS if kind == "opportunity" else FACTORS


def parse_weights(text: str | None, kind: str = "gap") -> dict[str, float]:
    """'people:0.4,severity:0.2' -> weights (defaults for factors not given)."""
    FACTORS = factors(kind)  # noqa: N806 - the factor list for this kind of zone
    weights = dict(config.OPPORTUNITY_WEIGHTS if kind == "opportunity" else config.PRIORITY_WEIGHTS)
    if not text:
        return weights
    for part in text.split(","):
        if not part.strip():
            continue
        key, _, value = part.partition(":")
        key = key.strip()
        if key not in FACTORS:
            raise ValueError(f"unknown factor {key!r}; choose from {FACTORS}")
        weight = float(value)
        if weight < 0:
            raise ValueError(f"weight for {key} must be >= 0")
        weights[key] = weight
    return weights


def raw_factors(zones: pd.DataFrame, kind: str = "gap") -> pd.DataFrame:
    """Raw factor values per zone (NaN = no data)."""
    raw = pd.DataFrame(index=zones.index)
    # An all-null column can come back as None objects, not NaN: make every factor numeric first.
    zones = zones.apply(lambda col: pd.to_numeric(col, errors="coerce") if col.name in NUMERIC else col)
    raw["people"] = zones.population
    if kind == "opportunity":
        raw["shortfall"] = zones.shortfall_pct
        raw["trend"] = -zones.trend_pct
        raw["operator_choice"] = 4 - zones.operators_mean
        return raw
    raw["severity"] = zones.severity
    raw["persistence"] = zones.persistence
    raw["trend"] = -zones.trend_pct
    raw["operator_choice"] = 4 - zones.operators_mean
    if "need" in zones:
        raw["need"] = zones["need"]
    return raw


def normalise(values: pd.Series) -> pd.Series:
    """Rank within the scope: min -> 0, max -> 1; NaN -> neutral 0.5."""
    known = values.dropna()
    if known.empty:
        return pd.Series(NEUTRAL, index=values.index)
    ranks = known.rank(method="min")
    n_unique = known.nunique()
    norm = (ranks - 1) / (len(known) - 1) if n_unique > 1 else pd.Series(1.0, index=known.index)
    return norm.reindex(values.index).fillna(NEUTRAL)


def confidence(tests: int) -> str:
    return "high" if tests >= 50 else "medium" if tests >= 10 else "low"


def score(zones: pd.DataFrame, weights: dict[str, float], kind: str = "gap") -> tuple[pd.DataFrame, dict]:
    """Zones with score, rank and per-factor breakdown; plus a summary of the weights used."""
    if zones.empty:
        return zones.assign(score=[], rank=[], breakdown=[]), {"factors_used": [], "weights": {}}
    FACTORS = factors(kind)  # noqa: N806
    raw = raw_factors(zones, kind)
    used = [f for f in FACTORS if f in raw and raw[f].notna().any() and weights.get(f, 0) > 0]
    total = sum(weights[f] for f in used) or 1.0
    norm = {f: normalise(raw[f]) for f in used}
    breakdowns, scores = [], []
    for i in zones.index:
        b = {}
        for f in used:
            n = float(norm[f][i])
            v = raw[f][i]
            b[f] = {"raw": None if pd.isna(v) else float(v), "normalised": round(n, 3),
                    "weight": round(weights[f] / total, 3), "contribution": round(100 * weights[f] * n / total, 1),
                    "no_data": bool(pd.isna(v))}
        breakdowns.append(b)
        scores.append(round(sum(x["contribution"] for x in b.values()), 1))
    out = zones.assign(score=scores, breakdown=breakdowns)
    out = out.sort_values(["score", "population"], ascending=False).reset_index(drop=True)
    out["rank"] = range(1, len(out) + 1)
    omitted = [f for f in FACTORS if f not in used]
    return out, {"factors_used": used, "weights": {f: round(weights[f] / total, 3) for f in used},
                 "omitted": {f: ("not built yet" if f == "need" else "weight 0 or no data") for f in omitted}}
