"""Data report, run through DuckDB: every table with row counts and freshness, one cross-source row per
sample place, and the live sources.

    python scripts/check_data.py             # the report -> docs/data_report.md (+ logs/check_data_report.json)
    python scripts/check_data.py --phases    # detailed checks: slowest cells, mast sites, Ofcom figures

Row counts and query times come from DuckDB over data/processed/ (one schema per table); data
periods, build dates, refresh cadence and licences from data/manifest.json.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import date
from pathlib import Path

import h3
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from coverage_detective.config import H3_RES, ROOT, SAMPLE_LOCATIONS  # noqa: E402
from coverage_detective.data import manifest  # noqa: E402
from coverage_detective.data.queries import cell_sql  # noqa: E402
from coverage_detective.data.db import QUERY_LOG, query  # noqa: E402

SOURCES = [  # schema, table, manifest source key, what it is
    ("CD_CELLS", "cells_r7", "cells", "H3 r7 grid, WorldPop population, nation"),
    ("CD_OOKLA_MOBILE", "ookla_mobile_r7", "ookla", "Ookla mobile speeds per cell, latest quarter"),
    ("CD_OOKLA_MOBILE_HISTORY", "ookla_mobile_history_r7", "ookla", "Ookla speeds per cell and quarter"),
    ("CD_OOKLA_MOBILE_TILES", "ookla_mobile_tiles", "ookla", "Ookla tiles (~400 m)"),
    ("CD_MASTS_SITES", "masts_sites", "masts", "Approximate mast sites (OpenCelliD, OSM, Ofcom register)"),
    ("CD_MASTS", "masts_r7", "masts", "Mast statistics per cell"),
    ("CD_WTR_POINTS", "wtr_points", "wtr", "Ofcom Wireless Telegraphy Register points"),
    ("CD_OFCOM_AREA", "ofcom_area_r7", "ofcom", "Ofcom Connected Nations per cell (constituency, council)"),
    ("CD_OFCOM_NATIONS", "ofcom_nations", "ofcom", "Ofcom Connected Nations per operator and nation"),
    ("CD_TERRAIN", "terrain_r7", "terrain", "Elevation per cell (Copernicus DEM)"),
    ("CD_LOS", "los_r7", "terrain", "Line of sight from cell centres"),
    ("CD_GAP_ZONES", "gap_zones", "zones", "Gap zones"),
    ("CD_GAP_ZONE_CELLS", "gap_zone_cells", "zones", "Gap zone cells"),
    ("CD_OPP_ZONES", "opportunity_zones", "opportunity_zones", "Improvement opportunities"),
    ("CD_OPP_ZONE_CELLS", "opportunity_zone_cells", "opportunity_zones", "Improvement opportunity cells"),
]

SLOWEST_SQL = """
SELECT c.cell_id, c.nation, round(c.lat, 3) AS lat, round(c.lon, 3) AS lon,
       round(c.population) AS population,
       round(o.download_mbps, 2) AS download_mbps, round(o.upload_mbps, 2) AS upload_mbps,
       round(o.latency_ms) AS latency_ms, o.tests, o.devices, o.quarter
FROM CD_CELLS.cells_r7 c
JOIN CD_OOKLA_MOBILE.ookla_mobile_r7 o ON o.cell_id = c.cell_id
WHERE c.population > 0 AND o.tests >= 10
ORDER BY o.download_mbps
LIMIT 10
"""


def table(rows: list[dict], columns: list[str]) -> str:
    """Rows as text, columns in the given order."""
    return pd.DataFrame(rows, columns=columns).to_string(index=False)


def check_phase_a() -> None:
    print("Phase A: 10 slowest populated cells with >= 10 tests (CD_CELLS x CD_OOKLA_MOBILE)")
    rows = query(SLOWEST_SQL)
    print(table(rows, ["cell_id", "nation", "lat", "lon", "population", "download_mbps",
                       "upload_mbps", "latency_ms", "tests", "devices", "quarter"]))
    entry = QUERY_LOG[-1]
    print(f"  {entry['rows']} rows in {entry['duration_ms']} ms")


def check_phase_b() -> None:
    print("\nPhase B: approximate mast sites by source and operator (CD_MASTS_SITES)")
    rows = query("SELECT sources, count(*) AS sites FROM CD_MASTS_SITES.masts_sites GROUP BY sources ORDER BY sites DESC")
    print(table(rows, ["sources", "sites"]))
    rows = query("""
        SELECT a.operator, a.sites, coalesce(c.sites, 0) AS confirmed_by_register
        FROM (SELECT operator, count(*) AS sites
              FROM (SELECT unnest(string_split(operators, ',')) AS operator FROM CD_MASTS_SITES.masts_sites)
              WHERE operator <> '' GROUP BY operator) a
        LEFT JOIN (SELECT operator, count(*) AS sites
                   FROM (SELECT unnest(string_split(operators_confirmed, ',')) AS operator FROM CD_MASTS_SITES.masts_sites)
                   WHERE operator <> '' GROUP BY operator) c ON c.operator = a.operator
        ORDER BY a.sites DESC""")
    print(table(rows, ["operator", "sites", "confirmed_by_register"]))
    rows = query("""SELECT licensee, operators, count(*) AS points, count(DISTINCT link_id) AS links,
                           round(quantile_cont(antenna_height_m, 0.5)) AS median_antenna_height_m
                    FROM CD_WTR_POINTS.wtr_points GROUP BY licensee, operators ORDER BY points DESC""")
    print("Ofcom licence register points (CD_WTR_POINTS):")
    print(table(rows, ["licensee", "operators", "points", "links", "median_antenna_height_m"]))

    print("\nPhase B: sample locations (CD_CELLS x CD_OOKLA_MOBILE x CD_MASTS)")
    ids = {h3.latlng_to_cell(lat, lon, H3_RES): name for name, lat, lon in SAMPLE_LOCATIONS}
    in_list = ", ".join(f"'{c}'" for c in ids)
    rows = query(f"""
        SELECT c.cell_id, round(c.population) AS population,
               round(o.download_mbps, 1) AS download_mbps, o.tests,
               round(m.nearest_mast_km, 2) AS nearest_mast_km,
               round(m.nearest_mast_bearing_deg) AS bearing_deg, m.nearest_mast_sources,
               m.masts_within_5km, m.masts_known_operator_within_5km,
               m.masts_unknown_operator_within_5km, m.masts_osm_only_within_5km,
               m.distinct_operators_within_5km, m.operators_within_5km,
               m.operators_confirmed_within_5km, m.wtr_sites_within_5km,
               m.microwave_backhaul_sites_within_5km, m.operator_data_status
        FROM CD_CELLS.cells_r7 c
        JOIN CD_MASTS.masts_r7 m ON m.cell_id = c.cell_id
        LEFT JOIN CD_OOKLA_MOBILE.ookla_mobile_r7 o ON o.cell_id = c.cell_id
        WHERE c.cell_id IN ({in_list})""")
    for r in rows:
        r["place"] = ids[r["cell_id"]]
    print(table(rows, ["place", "population", "download_mbps", "tests", "nearest_mast_km", "bearing_deg",
                       "nearest_mast_sources", "masts_within_5km", "masts_known_operator_within_5km",
                       "masts_unknown_operator_within_5km", "masts_osm_only_within_5km",
                       "distinct_operators_within_5km", "operators_within_5km", "operators_confirmed_within_5km",
                       "wtr_sites_within_5km", "microwave_backhaul_sites_within_5km", "operator_data_status"]))


def check_phase_e() -> None:
    print("\nPhase E: Ofcom area coverage for the sample locations (CD_OFCOM_AREA)")
    ids = {h3.latlng_to_cell(lat, lon, H3_RES): name for name, lat, lon in SAMPLE_LOCATIONS}
    in_list = ", ".join(f"'{c}'" for c in ids)
    rows = query(f"""
        SELECT cell_id, pcon_name, laua_name, g4_geo_out_0, g4_geo_out_1, g4_prem_in_0,
               g4_abrd_in_0, g5hc_prem_out_at_least_one, data_date
        FROM CD_OFCOM_AREA.ofcom_area_r7 WHERE cell_id IN ({in_list})""")
    for r in rows:
        r["place"] = ids[r["cell_id"]]
    print(table(rows, ["place", "pcon_name", "laua_name", "g4_geo_out_0", "g4_geo_out_1", "g4_prem_in_0",
                       "g4_abrd_in_0", "g5hc_prem_out_at_least_one", "data_date"]))
    rows = query("""
        SELECT location, operator, coverage_pct FROM CD_OFCOM_NATIONS.ofcom_nations
        WHERE technology = '4G' AND coverage_type = 'Geographic' AND rurality = 'Rural'
          AND operator IN ('EE', 'O2', 'Three', 'Vodafone') AND location <> 'UK'""")
    print("4G geographic coverage, rural, by operator (%):")
    print(pd.DataFrame(rows).pivot(index="location", columns="operator", values="coverage_pct").to_string())


def report() -> dict:
    m = manifest.load()["sources"]
    today = date.today()
    sources = []
    for db, table_name, key, what in SOURCES:
        t = time.perf_counter()
        n = query(f"SELECT count(*) AS n FROM {db}.{table_name}")[0]["n"]
        src = m.get(key, {})
        meta = manifest.evidence_meta(table_name) if key in m else {"data_period": None, "as_of": None}
        as_of = meta.get("as_of")
        sources.append({"database": db, "table": table_name, "what": what, "rows": int(n),
                        "query_ms": round((time.perf_counter() - t) * 1000), "data_period": meta.get("data_period"),
                        "as_of": as_of, "age_days": (today - date.fromisoformat(as_of[:10])).days if as_of else None,
                        "refresh": src.get("refresh"), "licence": src.get("licence")})
        print(f"  {db}.{table_name}: {int(n):,} rows ({sources[-1]['query_ms']} ms), {meta.get('data_period')}")
    places = []
    for name, lat, lon in SAMPLE_LOCATIONS:
        cid = h3.latlng_to_cell(lat, lon, H3_RES)
        sql, _ = cell_sql(cid)
        t = time.perf_counter()
        r = (query(sql) or [{}])[0]
        places.append({"place": name, "cell_id": cid, "query_ms": round((time.perf_counter() - t) * 1000),
                       "population": r.get("population"), "download_mbps": r.get("download_mbps"), "tests": r.get("tests"),
                       "quarters_with_data": r.get("quarters_with_data"), "nearest_mast_km": r.get("nearest_mast_km"),
                       "masts_within_5km": r.get("masts_within_5km"), "operators_within_5km": r.get("operators_within_5km"),
                       "operator_data_status": r.get("operator_data_status"), "laua_name": r.get("laua_name"),
                       "g4_geo_out_0": r.get("g4_geo_out_0"), "elevation_m": r.get("elevation_mean_m"),
                       "los_class": r.get("los_class")})
    from coverage_detective.sources import flood_warnings, geocode, met_office_warnings, weather
    live_places = []
    for name, lat, lon in SAMPLE_LOCATIONS:
        g = geocode.reverse(lat, lon)
        w, mo = weather.forecast(lat, lon), met_office_warnings.for_place(g.data.get("nearest"), lat, lon)
        f = flood_warnings.near(lat, lon, (g.data.get("nearest") or {}).get("country"))
        live_places.append({"place": name, "geocode": g.status, "weather": f"{w.status}: {w.data.get('severity')}",
                            "met_office": f"{mo.status}: {mo.data.get('count')} warnings ({mo.data.get('region')})",
                            "floods": f"{f.status}: {f.data.get('count')}"})
    return {"generated": time.strftime("%Y-%m-%d %H:%M"), "sources": sources, "places": places,
            "live_sources": live_places}


def markdown(r: dict) -> str:
    out = [f"# Data report\n\nGenerated {r['generated']} by `scripts/check_data.py`: every query below ran on DuckDB "
           "over the Parquet files in data/processed/. Freshness from `data/manifest.json`.\n",
           "## Sources\n", "| Table | Rows | Query | Data period | As of | Age | Refresh | Licence |", "|---|---:|---:|---|---|---:|---|---|"]
    for s in r["sources"]:
        out.append(f"| `{s['database']}.{s['table']}` ({s['what']}) | {s['rows']:,} | {s['query_ms']} ms | {s['data_period'] or ''} | "
                   f"{s['as_of'] or ''} | {s['age_days'] if s['age_days'] is not None else ''} d | {s['refresh'] or ''} | {s['licence'] or ''} |")
    out += ["\n## Sample places: one cross-source row each\n",
            "One query per place joins cells, Ookla (latest + history + neighbours), masts, Ofcom area, terrain and line of sight.\n",
            "| Place | People | Mbps | Tests | Quarters | Nearest site | Sites in 5 km | Operators | Operator data | Council | 4G not-spot land | Elevation | Line of sight (cell centre) | Query |",
            "|---|---:|---:|---:|---:|---:|---:|---|---|---|---:|---:|---|---:|"]
    f = lambda v, d=1: "" if v is None else (f"{v:,.{d}f}" if isinstance(v, (int, float)) else str(v))  # noqa: E731
    for p in r["places"]:
        out.append(f"| {p['place']} | {f(p['population'], 0)} | {f(p['download_mbps'])} | {f(p['tests'], 0)} | {f(p['quarters_with_data'], 0)} | "
                   f"{f(p['nearest_mast_km'], 2)} km | {f(p['masts_within_5km'], 0)} | {p['operators_within_5km'] or ''} | {p['operator_data_status'] or ''} | "
                   f"{p['laua_name'] or ''} | {f(p['g4_geo_out_0'])}% | {f(p['elevation_m'], 0)} m | {p['los_class'] or ''} | {p['query_ms']} ms |")
    out += ["\n## Live sources for the sample places\n", "| Place | Geocode | Weather | Met Office | EA floods |", "|---|---|---|---|---|"]
    for p in r["live_sources"]:
        out.append(f"| {p['place']} | {p['geocode']} | {p['weather']} | {p['met_office']} | {p['floods']} |")
    return "\n".join(out) + "\n"


def main() -> None:
    if "--phases" in sys.argv:
        check_phase_a()
        check_phase_b()
        check_phase_e()
        return
    r = report()
    (ROOT / "logs").mkdir(exist_ok=True)
    (ROOT / "logs" / "check_data_report.json").write_text(json.dumps(r, indent=1, default=str))
    md = markdown(r)
    (ROOT / "docs").mkdir(exist_ok=True)
    (ROOT / "docs" / "data_report.md").write_text(md)
    print(md)


if __name__ == "__main__":
    main()
