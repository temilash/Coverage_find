"""The data layer: DuckDB over Parquet. Every dataset query goes through here.

    from coverage_detective.data.db import query
    rows = query("SELECT cell_id, population FROM CD_CELLS.cells_r7 LIMIT 5")

Each dataset is a DuckDB schema with one view over its Parquet file (TABLES below), e.g.
CD_CELLS.cells_r7 -> data/processed/cells_r7.parquet. In sample mode (SAMPLE_MODE=true or
`python run.py --sample`) the same views point at the small committed extracts in sample_data/db/,
so the app and the tests run offline. A table whose file is missing is left out (the query
naming it fails with a clear error).

Every query is appended to logs/queries.jsonl (time, SQL, duration, row count, error), and the
latest entries are kept in QUERY_LOG so evidence bundles can show the SQL behind each figure.
"""

from __future__ import annotations

import json
import math
import threading
import time
from collections import deque
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import duckdb

from coverage_detective.config import PROCESSED, ROOT, SAMPLES, sample_mode

SAMPLE_DIR = SAMPLES / "db"
LOG_PATH = ROOT / "logs" / "queries.jsonl"
QUERY_LOG: deque[dict] = deque(maxlen=500)

# (schema, table): the view CD_X.table reads <data dir>/<table>.parquet
TABLES = [
    ("CD_CELLS", "cells_r7"),
    ("CD_OOKLA_MOBILE", "ookla_mobile_r7"),
    ("CD_OOKLA_MOBILE_HISTORY", "ookla_mobile_history_r7"),
    ("CD_OOKLA_MOBILE_TILES", "ookla_mobile_tiles"),
    ("CD_MASTS_SITES", "masts_sites"),
    ("CD_MASTS", "masts_r7"),
    ("CD_WTR_POINTS", "wtr_points"),
    ("CD_OFCOM_AREA", "ofcom_area_r7"),
    ("CD_OFCOM_NATIONS", "ofcom_nations"),
    ("CD_TERRAIN", "terrain_r7"),
    ("CD_LOS", "los_r7"),
    ("CD_GAP_ZONES", "gap_zones"),
    ("CD_GAP_ZONE_CELLS", "gap_zone_cells"),
    ("CD_OPP_ZONES", "opportunity_zones"),
    ("CD_OPP_ZONE_CELLS", "opportunity_zone_cells"),
]


class DBError(RuntimeError):
    """A query failed; the message says why."""


_lock = threading.Lock()
_con: duckdb.DuckDBPyConnection | None = None
_con_dir: Path | None = None


def data_dir() -> Path:
    return SAMPLE_DIR if sample_mode() else PROCESSED


def available() -> list[tuple[str, str, Path]]:
    """(schema, table, file) for every table whose Parquet file exists."""
    d = data_dir()
    return [(s, t, d / f"{t}.parquet") for s, t in TABLES if (d / f"{t}.parquet").exists()]


def connect() -> duckdb.DuckDBPyConnection:
    """The shared connection with every available view (rebuilt if sample mode was switched)."""
    global _con, _con_dir
    with _lock:
        if _con is None or _con_dir != data_dir():
            con = duckdb.connect()
            for schema, table, path in available():
                con.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
                con.execute(f"CREATE OR REPLACE VIEW {schema}.{table} AS SELECT * FROM read_parquet('{path}')")
            _con, _con_dir = con, data_dir()
        return _con


def reset() -> None:
    """Forget the connection (after a rebuild adds or replaces files)."""
    global _con
    with _lock:
        _con = None


def _value(v):
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, float) and math.isnan(v):
        return None
    return v


def _log(sql: str, started: float, rows: int | None, error: str | None) -> dict:
    entry = {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sql": " ".join(sql.split()),
        "duration_ms": round(1000 * (time.perf_counter() - started)),
        "rows": rows,
        "error": error,
    }
    QUERY_LOG.append(entry)
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass  # a read-only checkout still answers queries
    return entry


def query(sql: str, params: list | None = None) -> list[dict]:
    """Run a SELECT and return every row as a dict."""
    started = time.perf_counter()
    try:
        cur = connect().cursor()  # one cursor per call: safe from the API's worker threads
        cur.execute(sql, params or [])
        cols = [d[0] for d in cur.description]
        rows = [{c: _value(v) for c, v in zip(cols, row)} for row in cur.fetchall()]
    except duckdb.Error as e:
        _log(sql, started, None, str(e))
        raise DBError(str(e)) from None
    _log(sql, started, len(rows), None)
    return rows


def status() -> str:
    """'ok', or what is missing."""
    if not available():
        return (f"no data in {data_dir()}: build it (scripts/process/) or run with --sample"
                if not sample_mode() else f"no sample extracts in {SAMPLE_DIR}")
    try:
        query("SELECT count(*) AS n FROM CD_CELLS.cells_r7")
    except DBError as e:
        return str(e)
    return "ok"


# ------------------------------------------------------------------ where sample mode has data

POINT_REACH_KM = 10.0  # a point's evidence reaches 10 km (sites, line of sight), so all of that must be extracted


def sample_areas() -> dict[str, list[float]]:
    """The extract boxes, {name: [west, south, east, north]} (sample places and example zones)."""
    try:
        return json.loads((SAMPLE_DIR / "extract.json").read_text())["boxes"]
    except (OSError, ValueError, KeyError):
        return {}


def sample_covers(lat: float, lon: float) -> str | None:
    """The extract box holding everything within POINT_REACH_KM of the point, or None."""
    dlat = POINT_REACH_KM / 111.0
    dlon = POINT_REACH_KM / (111.32 * math.cos(math.radians(lat)))
    for name, (w, s, e, n) in sample_areas().items():
        if w <= lon - dlon and lon + dlon <= e and s <= lat - dlat and lat + dlat <= n:
            return name
    return None


def sample_coverage_message() -> str:
    """What sample mode can answer, for a refused request."""
    boxes = ", ".join(f"{name.replace('_', ' ')} (lat {(s + n) / 2:.3f}, lon {(w + e) / 2:.3f})"
                      for name, (w, s, e, n) in sample_areas().items())
    return ("Sample mode only has data around the example places and zones: " + boxes
            + ". Points must lie well inside one of these areas (their 10 km neighbourhood is needed for mast sites "
            "and line of sight). Elsewhere, build the full data and run python run.py.")
