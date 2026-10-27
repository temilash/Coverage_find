"""Paths and settings shared by every script and module."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
PROCESSED = ROOT / "data" / "processed"
SAMPLES = ROOT / "sample_data"

H3_RES = 7

BOUNDARY_PATH = RAW / "boundary" / "uk_countries.geojson"
POPULATION_PATH = RAW / "population" / "population.tif"
CELLS_PATH = PROCESSED / "cells_r7.parquet"

# UK bounding box (lon/lat), a little generous: Shetland to Scilly, NI to Lowestoft.
UK_BBOX = {"min_lon": -8.7, "min_lat": 49.8, "max_lon": 1.9, "max_lat": 60.9}

load_dotenv(ROOT / ".env")


def env(name: str, default: str | None = None) -> str | None:
    """A setting from the environment or .env; empty strings count as unset."""
    value = os.environ.get(name, "").strip()
    return value or default


def sample_mode() -> bool:
    return (env("SAMPLE_MODE", "false") or "").lower() in ("1", "true", "yes")


# Sample locations used by every check: (name, lat, lon). London is postcode WC2N 5DU
# (Trafalgar Square), coordinates from postcodes.io.
SAMPLE_LOCATIONS = [
    ("CO9 1TP, Gosfield, Essex", 51.935645, 0.592832),  # postcodes.io; the first sample everywhere
    ("Great Langdale, Cumbria", 54.445, -3.105),
    ("Central London (WC2N 5DU)", 51.507211, -0.128269),
    ("Near Rhayader, Powys", 52.30, -3.51),
    ("Sutton St Edmund, Lincolnshire", 52.700117, 0.024085),
    ("Remote cell near Bettws, Powys (no tests, no site within 10 km)", 52.399095, -3.186185),
]

# Gap zones (Council view). A cell with >= GAP_MIN_CELL_POPULATION people is "poorly served"
# when (a) the test-weighted
# download over the cell and its neighbour ring is below GAP_SLOW_MBPS from at least
# GAP_MIN_TESTS tests, or (b) no approximate site lies within 5 km (unless >= GAP_MIN_TESTS
# tests measure >= GAP_SLOW_MBPS: a gap in our site data, not coverage), or (c) the line of sight
# from the cell centre is blocked by >= GAP_BLOCKED_M to every site within 5 km and the
# cell + ring has fewer than GAP_MIN_TESTS tests. Zones (connected poorly served cells) are kept
# when they hold >= GAP_MIN_ZONE_POPULATION people: a per-cell threshold would drop sparse
# valleys whose people are spread over several cells (Great Langdale: 48 + 17 people).
# See coverage_detective/planning/zones.py.
GAP_MIN_CELL_POPULATION = 10
GAP_MIN_ZONE_POPULATION = 50
GAP_SLOW_MBPS = 5.0
GAP_MIN_TESTS = 10
GAP_BLOCKED_M = 10.0
GAP_SITES_CHECKED = 8      # nearest sites within 5 km checked for criterion (c)

# Default priority weights (the API accepts others). "need" applies only when the
# rural-urban / deprivation data is built; missing factors are left out and the
# remaining weights rescaled.
PRIORITY_WEIGHTS = {"people": 0.35, "severity": 0.25, "persistence": 0.15, "trend": 0.10, "operator_choice": 0.15,
                    "need": 0.0}

# Improvement opportunities (Council view): populated cells that are NOT poorly served but have a
# measurable weakness, clustered like gap zones (zones >= GAP_MIN_ZONE_POPULATION people). A cell
# can have several reasons (all listed); see coverage_detective/planning/opportunities.py.
#   slow         ring speed (>= GAP_MIN_TESTS tests) below OPP_SHORTFALL x the lower of the area
#                (local authority) median and the UK median
#   falling      download fell by OPP_FALL_PCT % or more over >= OPP_FALL_QUARTERS quarters (>= 10 tests)
#   few_operators only 1-2 operators identified within 5 km and no sites with unknown operators
#   capacity     >= OPP_CAPACITY_MIN_POP people, ring speed below OPP_CAPACITY_SPEED x UK median, and
#                devices per head >= the UK 90th percentile or a fall of OPP_CAPACITY_FALL_PCT % or more
# Zones with fewer than GAP_MIN_TESTS tests are marked "limited data".
OPP_SHORTFALL = 0.5
OPP_FALL_PCT = -30.0
OPP_FALL_QUARTERS = 3
OPP_CAPACITY_MIN_POP = 1000
OPP_CAPACITY_SPEED = 0.75
OPP_CAPACITY_FALL_PCT = -20.0
OPPORTUNITY_WEIGHTS = {"people": 0.35, "shortfall": 0.30, "trend": 0.15, "operator_choice": 0.20}

# Council view map: how much is listed at each zoom (the list always matches the map viewport).
COUNCIL_VIEW = [  # (min zoom, max gap zones, max opportunities; 0 = none shown)
    (0.0, 40, 0),
    (8.0, 40, 0),
    (10.0, 100, 60),
]
