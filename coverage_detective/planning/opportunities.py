"""Improvement opportunities: populated cells that are NOT poorly served (not in a gap zone) but have
a measurable weakness, clustered into zones the same way as gap zones (>= 50 people per zone).

Reasons per cell (thresholds in config.py; a cell can have several):
  slow           ring speed (>= 10 tests over the cell + its 6 neighbours) below OPP_SHORTFALL x the lower
                 of the local authority median and the UK median (cells with >= 10 tests)
  falling        download fell by >= 30% from the oldest to the latest quarter, >= 3 quarters, >= 10 tests
  few_operators  only 1-2 operators identified within 5 km and no sites with unknown operators nearby
                 (so the low count is not just missing operator data)
  capacity       >= 1,000 people, ring speed below 75% of the UK median, and devices per head at or
                 above the UK 90th percentile or a fall of >= 20%

Per zone: people with each reason, the reasons present, shortfall (population-weighted share by which
measured cells fall below their reference median), trend, operators and tests. Zones with fewer than
10 tests are "limited data". Scoring: scoring.py (opportunity factors).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from coverage_detective import config
from coverage_detective.planning import zones as Z

REASONS = ["slow", "falling", "few_operators", "capacity"]
REASON_LABELS = {"slow": "Speed well below the median", "falling": "Speeds falling", "few_operators": "Only 1-2 operators",
                 "capacity": "Busy network (capacity)"}


def medians(df: pd.DataFrame) -> tuple[float, pd.Series]:
    """UK median and per local authority median of cell download (cells with >= 10 tests)."""
    measured = df[df.tests.fillna(0) >= config.GAP_MIN_TESTS]
    return float(measured.download_mbps.median()), measured.groupby("laua_code").download_mbps.median()


def classify(df: pd.DataFrame, uk_median: float, la_median: pd.Series, devices_p90: float) -> pd.DataFrame:
    """Add opp_<reason> flags, ref_mbps (the lower median) and `opportunity` to a classified per-cell frame
    (zones.classify must have run: `poor` marks gap cells, which are never opportunities)."""
    populated = (df.population >= config.GAP_MIN_CELL_POPULATION) & ~df.poor
    measured = df.ring_tests >= config.GAP_MIN_TESTS
    df["area_median_mbps"] = df.laua_code.map(la_median)
    df["ref_mbps"] = np.fmin(df.area_median_mbps.fillna(uk_median), uk_median)
    df["opp_slow"] = populated & measured & (df.ring_mbps < config.OPP_SHORTFALL * df.ref_mbps)
    df["opp_falling"] = (populated & (df.tests.fillna(0) >= config.GAP_MIN_TESTS)
                         & (df.quarters_with_data.fillna(0) >= config.OPP_FALL_QUARTERS)
                         & (df.download_change_pct <= config.OPP_FALL_PCT))
    df["opp_few_operators"] = (populated & df.distinct_operators_within_5km.between(1, 2)
                               & (df.masts_unknown_operator_within_5km.fillna(0) == 0))
    df["opp_capacity"] = (populated & (df.population >= config.OPP_CAPACITY_MIN_POP) & measured
                          & (df.ring_mbps < config.OPP_CAPACITY_SPEED * uk_median)
                          & ((df.devices_per_1k_pop >= devices_p90) | (df.download_change_pct <= config.OPP_CAPACITY_FALL_PCT)))
    df["opportunity"] = df[[f"opp_{r}" for r in REASONS]].any(axis=1)
    df["shortfall"] = np.where(measured, np.clip((df.ref_mbps - df.ring_mbps) / df.ref_mbps, 0, 1), np.nan)
    return df


def build(df: pd.DataFrame, history: pd.DataFrame, places: pd.DataFrame | None, uk_median: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(zones, zone_cells) for improvement opportunities, from the per-cell frame after classify()."""
    opp = df[df.opportunity].set_index("cell_id")
    rows, cell_rows = [], []
    for cells in Z.components(opp.index.tolist()):
        z = opp.loc[cells]
        pop = float(z.population.sum())
        if pop < config.GAP_MIN_ZONE_POPULATION:
            continue
        w = z.population / pop
        lat, lon = float((z.lat * w).sum()), float((z.lon * w).sum())
        _, trend, quarters = Z.zone_quarters(cells, history)
        tests = int(z.tests.fillna(0).sum())
        speed_cells = z[z.tests.fillna(0) > 0]
        mbps = float((speed_cells.download_mbps * speed_cells.tests).sum() / speed_cells.tests.sum()) if len(speed_cells) else None
        sf = z[z.shortfall.notna()]
        las = z.groupby(["laua_code", "laua_name"]).population.sum().sort_values(ascending=False)
        ops = sorted({o for v in z.operators_within_5km.fillna("") for o in v.split(",") if o})
        name, place_type = Z.name_zone(cells, places, lat, lon)
        rows.append({
            "cells": cells, "n_cells": len(cells), "population": pop, "centroid_lat": lat, "centroid_lon": lon,
            "nation": z.nation.mode().iloc[0], "la_codes": ",".join(c for c, _ in las.index),
            "la_names": "|".join(n for _, n in las.index), "primary_la_code": las.index[0][0] if len(las) else None,
            "primary_la_name": las.index[0][1] if len(las) else None, "place_name": name, "place_type": place_type,
            **{f"pop_{r}": float(z.population[z[f"opp_{r}"]].sum()) for r in REASONS},
            "reasons": ",".join(r for r in REASONS if z[f"opp_{r}"].any()),
            "shortfall_pct": float((sf.shortfall * sf.population).sum() / sf.population.sum() * 100) if len(sf) else None,
            "ref_mbps": float((z.ref_mbps * w).sum()), "uk_median_mbps": uk_median,
            "trend_pct": trend, "quarters_with_data": quarters,
            "operators_mean": float((z.distinct_operators_within_5km.fillna(0) * w).sum()), "operators_nearby": ",".join(ops),
            "tests": tests, "download_mbps": mbps,
            "median_cell_mbps": float(speed_cells.download_mbps.median()) if len(speed_cells) else None,
            "ring_mbps": float((z.ring_mbps * z.ring_tests).sum() / z.ring_tests.sum()) if z.ring_tests.sum() else None,
            "ring_tests": int(z.ring_tests.sum()), "limited_data": tests < config.GAP_MIN_TESTS,
        })
    zones = pd.DataFrame(rows).sort_values("population", ascending=False).reset_index(drop=True)
    zones.insert(0, "zone_id", [f"O{i + 1:05d}" for i in range(len(zones))])
    zones["name"] = [(n[0].upper() + n[1:]) if pd.notna(n) else f"Area in {la}" if pd.notna(la) else "Unnamed area"
                     for n, la in zip(zones.place_name, zones.primary_la_name)]
    for zid, cells in zip(zones.zone_id, zones.cells):
        for c in cells:
            r = opp.loc[c]
            cell_rows.append({"cell_id": c, "zone_id": zid, "population": float(r.population), "lat": r.lat, "lon": r.lon,
                              **{r_: bool(r[f"opp_{r_}"]) for r_ in REASONS}, "ring_mbps": r.ring_mbps,
                              "ring_tests": int(r.ring_tests), "ref_mbps": float(r.ref_mbps),
                              "tests": int(r.tests) if pd.notna(r.tests) else 0, "laua_code": r.laua_code})
    return zones.drop(columns="cells"), pd.DataFrame(cell_rows)
