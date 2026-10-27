"""Sample mode's stand-in for Zetaris: DuckDB over the committed extracts in sample_data/zetaris/.

Each Zetaris source (CD_CELLS.cells_r7, ...) is a DuckDB schema + view over its extract, so the
same SQL runs unchanged apart from three Spark functions, translated here:
  percentile_approx(x, q)              -> quantile_cont(x, q)
  collect_list(x)                      -> list(x)
  named_struct('a', x, 'b', y)         -> struct_pack(a := x, b := y)
The UK-wide context query (medians over every cell) is answered from uk_context.json, saved from
the full data when the extracts were built, so medians match the full data.
Extracts: scripts/build_samples.py (cells, sites, tiles and zones around the example places).
"""

from __future__ import annotations

import json
import re
import threading
from decimal import Decimal

from coverage_detective.config import SAMPLES

EXTRACTS = SAMPLES / "zetaris"
_lock = threading.Lock()
_con = None


def _connect():
    global _con
    if _con is None:
        import duckdb

        con = duckdb.connect()
        tables = json.loads((EXTRACTS / "extract.json").read_text())["tables"]
        for t in tables:
            con.execute(f"CREATE SCHEMA IF NOT EXISTS {t['database']}")
            con.execute(f"CREATE VIEW {t['database']}.{t['table']} AS SELECT * FROM read_parquet('{EXTRACTS / t['file']}')")
        _con = con
    return _con


def _named_struct(sql: str) -> str:
    out, i = [], 0
    while (j := sql.find("named_struct(", i)) >= 0:
        out.append(sql[i:j])
        k, depth = j + len("named_struct("), 1
        start = k
        while depth:
            depth += {"(": 1, ")": -1}.get(sql[k], 0)
            k += 1
        args = [a.strip() for a in re.split(r",(?![^()]*\))", sql[start:k - 1])]
        pairs = [f"{args[n].strip(chr(39))} := {args[n + 1]}" for n in range(0, len(args), 2)]
        out.append("struct_pack(" + ", ".join(pairs) + ")")
        i = k
    return "".join(out) + sql[i:]


def translate(sql: str) -> str:
    sql = re.sub(r"\bpercentile_approx\(", "quantile_cont(", sql)
    sql = re.sub(r"\bcollect_list\(", "list(", sql)
    return _named_struct(sql)


def _value(v):
    return float(v) if isinstance(v, Decimal) else v


def query(sql: str) -> list[dict]:
    from coverage_detective.evidence import UK_CONTEXT_SQL

    if sql.strip() == UK_CONTEXT_SQL.strip():
        return json.loads((EXTRACTS / "uk_context.json").read_text())
    with _lock:  # one DuckDB connection, used from the API's worker threads
        cur = _connect().execute(translate(sql))
        cols = [d[0] for d in cur.description]
        return [{c: _value(v) for c, v in zip(cols, row)} for row in cur.fetchall()]


# ------------------------------------------------------------------ where sample mode has data

POINT_REACH_KM = 10.0  # a point's evidence reaches 10 km (sites, line of sight), so all of that must be extracted


def areas() -> dict[str, list[float]]:
    """The extract boxes, {name: [west, south, east, north]} (sample places and example zones)."""
    try:
        return json.loads((EXTRACTS / "extract.json").read_text())["boxes"]
    except (OSError, ValueError, KeyError):
        return {}


def covers(lat: float, lon: float) -> str | None:
    """The extract box holding everything within POINT_REACH_KM of the point, or None (no full evidence there)."""
    import math

    dlat = POINT_REACH_KM / 111.0
    dlon = POINT_REACH_KM / (111.32 * math.cos(math.radians(lat)))
    for name, (w, s, e, n) in areas().items():
        if w <= lon - dlon and lon + dlon <= e and s <= lat - dlat and lat + dlat <= n:
            return name
    return None


def coverage_message() -> str:
    """What sample mode can diagnose, for a refused request."""
    boxes = ", ".join(f"{name.replace('_', ' ')} (lat {(s + n) / 2:.3f}, lon {(w + e) / 2:.3f})"
                      for name, (w, s, e, n) in areas().items())
    return ("Sample mode only has data around the example places and zones: " + boxes
            + ". Points must lie well inside one of these areas (their 10 km neighbourhood is needed for mast sites "
            "and line of sight). Elsewhere, run the full app with Zetaris (python run.py).")
