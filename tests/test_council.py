"""Gap zones, priority scoring and siting rules (synthetic data, no database or DEM files)."""

import h3
import numpy as np
import pandas as pd
import pytest
from fastapi import HTTPException

from coverage_detective.api.council import scope_filter
from coverage_detective.planning import scoring, siting
from coverage_detective.planning import zones as Z

BASE = h3.latlng_to_cell(54.445, -3.105, 7)


def cells_frame(rows):
    cols = ["cell_id", "population", "ring_mbps", "ring_tests", "masts_within_5km", "blocked_all"]
    return pd.DataFrame(rows, columns=cols)


def test_criteria_and_site_data_gap():
    ring = sorted(h3.grid_ring(BASE, 1))
    df = Z.classify(cells_frame([
        (BASE, 100, 3.0, 20, 4, False),       # (a) slow, measured
        (ring[0], 100, np.nan, 0, 0, False),  # (b) no site, no tests
        (ring[1], 100, 40.0, 30, 0, False),   # no site but 40 Mbps measured: site data gap, not poor
        (ring[2], 100, 9.0, 3, 5, True),      # (c) blocked to every site, thin data
        (ring[3], 100, 9.0, 30, 5, True),     # blocked but well tested at 9 Mbps: not poor
        (ring[4], 5, np.nan, 0, 0, False),    # too few people
    ]))
    by = df.set_index("cell_id")
    assert by.loc[BASE, "slow"] and by.loc[BASE, "severity"] == pytest.approx(0.4)
    assert by.loc[ring[0], "no_site"] and by.loc[ring[0], "severity"] == 1.0
    assert not by.loc[ring[1], "poor"] and by.loc[ring[1], "site_data_gap"]
    assert by.loc[ring[2], "blocked"] and by.loc[ring[2], "severity"] == 0.75
    assert not by.loc[ring[3], "poor"] and not by.loc[ring[4], "poor"]


def test_components_follow_h3_neighbours():
    far = h3.latlng_to_cell(51.5, -0.12, 7)
    ring = sorted(h3.grid_ring(BASE, 1))
    groups = Z.components([BASE, ring[0], ring[3], far])
    assert sorted(len(g) for g in groups) == [1, 3]


def test_zone_population_threshold_keeps_sparse_valleys():
    ring = sorted(h3.grid_ring(BASE, 1))
    lat, lon = h3.cell_to_latlng(BASE)
    df = pd.DataFrame({
        "cell_id": [BASE, ring[0], h3.latlng_to_cell(51.5, -0.12, 7)],
        "population": [30.0, 25.0, 40.0], "lat": [lat] * 3, "lon": [lon] * 3, "nation": "England",
        "laua_code": "E06000064", "laua_name": "Westmorland and Furness",
        "poor": True, "slow": False, "no_site": False, "blocked": True, "severity": 0.75,
        "ring_mbps": np.nan, "ring_tests": 2, "tests": 0, "download_mbps": np.nan,
        "distinct_operators_within_5km": 2, "operators_within_5km": "EE,Vodafone",
    })
    history = pd.DataFrame(columns=["cell_id", "quarter", "download_mbps", "tests"])
    zones, cells = Z.build(df, history, None)
    assert len(zones) == 1 and zones.population.iloc[0] == 55.0  # 30 + 25 together; the lone 40 is dropped
    assert set(cells.cell_id) == {BASE, ring[0]} and zones.persistence.isna().all()


def test_normalise_and_score():
    norm = scoring.normalise(pd.Series([10.0, 30.0, np.nan, 20.0]))
    assert norm.tolist() == [0.0, 1.0, 0.5, 0.5]
    zones = pd.DataFrame({"zone_id": ["A", "B"], "population": [1000.0, 100.0], "severity": [0.5, 1.0],
                          "persistence": [np.nan, 1.0], "trend_pct": [-20.0, 5.0], "operators_mean": [1.0, 3.0]})
    scored, summary = scoring.score(zones, scoring.parse_weights("people:0.5,severity:0.5,persistence:0,trend:0,operator_choice:0"))
    assert summary["factors_used"] == ["people", "severity"] and summary["weights"] == {"people": 0.5, "severity": 0.5}
    assert "need" in summary["omitted"]
    assert scored.score.tolist() == [50.0, 50.0]  # each wins one factor
    for _, r in scored.iterrows():
        assert r.score == pytest.approx(sum(b["contribution"] for b in r.breakdown.values()))
    scored, _ = scoring.score(zones, scoring.parse_weights(None))
    assert scored.zone_id.iloc[0] == "A"  # more people, worsening trend, fewer operators


def test_weights_validation():
    with pytest.raises(ValueError):
        scoring.parse_weights("money:1")
    with pytest.raises(ValueError):
        scoring.parse_weights("people:-1")
    assert scoring.parse_weights("people:2")["people"] == 2.0


def test_scope_filter_rejects_injection():
    assert scope_filter("uk") == "1 = 1"
    assert scope_filter("nation:Wales", "z") == "z.nation = 'Wales'"
    assert "E06000064" in scope_filter("la:E06000064")
    for bad in ["la:E06000064' OR '1'='1", "nation:Atlantis", "la:XYZ"]:
        with pytest.raises(HTTPException):
            scope_filter(bad)


ZONE = {"population": 100.0, "pop_no_site": 0.0, "pop_blocked": 0.0, "pop_slow": 0.0, "operators_mean": 3.0, "tests": 40}


def test_fix_rules():
    assert siting.suggest_fix({**ZONE, "pop_blocked": 80.0, "tests": 2}, 3)["type"] == "new_site"
    fix = siting.suggest_fix({**ZONE, "pop_blocked": 80.0, "tests": 2}, 3)
    assert fix["secondary"] == "gather_data" and "(0 people)" not in " ".join(fix["why"])
    assert siting.suggest_fix({**ZONE, "operators_mean": 1.2}, 2)["type"] == "add_operators"
    assert siting.suggest_fix({**ZONE, "pop_slow": 70.0}, 2)["type"] == "capacity_upgrade"
    assert siting.suggest_fix({**ZONE, "tests": 3}, 2)["type"] == "gather_data"


class RidgeDEM:
    """Flat ground at 100 m with a 300 m ridge along latitude 54.0."""

    def elevation(self, lats, lons):
        lats = np.asarray(lats)
        return np.where(np.abs(lats - 54.0) < 0.002, 400.0, 100.0).astype(np.float32)


def test_evaluate_counts_people_with_a_clear_line():
    zone_cells = [{"cell_id": "north", "lat": 54.02, "lon": -3.0, "population": 60.0}]
    area = [{"cell_id": "south", "lat": 53.98, "lon": -3.0, "population": 500.0, "poor": False},
            {"cell_id": "north2", "lat": 54.03, "lon": -3.0, "population": 40.0, "poor": True}]
    cands = [{"kind": "new_site", "lat": 54.025, "lon": -3.0, "mast_height_m": 25.0}]
    out = siting.evaluate(cands, zone_cells, area, dem=RidgeDEM())[0]
    # North of the ridge: both northern cells are clear; the southern one is behind the ridge.
    assert {r["cell_id"] for r in out["reached"]} == {"north", "north2"}
    assert out["zone_people_gaining"] == 60 and out["poor_people_gaining"] == 100 and out["people_reached"] == 100
