"""Council view API: gap zones and improvement opportunities, ranked by a transparent score, and per-zone fixes.

  GET /api/council/scopes                        nations and local authorities (gap and opportunity counts, bounds)
  GET /api/council/view?bbox=&zoom=&scope=&show= the zones on the map now (zoom caps in config.COUNCIL_VIEW)
  GET /api/council/zones?scope=&type=&weights=   ranked zones with each factor's contribution
  GET /api/council/zones.csv?scope=&type=        the same ranking as CSV
  GET /api/council/zones/{zone_id}               zone details, cells, evidence summary, fix, candidates
  GET /api/council/zones/{zone_id}/cells         just the zone's cells (for Explore's ?zone= outline)

Two zone types: gap zones (Z00001; poorly served cells, planning/zones.py) and improvement opportunities
(O00001; cells with a measurable weakness, planning/opportunities.py). scope: uk | nation:<name> |
la:<local authority code>. weights / opp_weights: e.g. people:0.4,severity:0.3 (see planning/scoring.py).
All data comes from DuckDB (CD_GAP_ZONES, CD_GAP_ZONE_CELLS, CD_OPP_ZONES, CD_OPP_ZONE_CELLS, CD_CELLS,
CD_MASTS_SITES, CD_OFCOM_AREA); every query is listed in the response.
"""

from __future__ import annotations

import csv
import io
import math
import re
import time
from functools import lru_cache

import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from coverage_detective import config
from coverage_detective.data.db import QUERY_LOG, DBError, query
from coverage_detective.planning import scoring, siting
from coverage_detective.planning.opportunities import REASON_LABELS, REASONS

router = APIRouter(prefix="/api/council")
NATIONS = ["England", "Scotland", "Wales", "Northern Ireland"]
SCOPE_RE = re.compile(r"^(uk|nation:(England|Scotland|Wales|Northern Ireland)|la:[EWSN]\d{8})$")
ZONE_RE = re.compile(r"^[ZO]\d{5}$")
KINDS = {
    "gap": {"zones": "CD_GAP_ZONES.gap_zones", "cells": "CD_GAP_ZONE_CELLS.gap_zone_cells", "label": "gap zone"},
    "opportunity": {"zones": "CD_OPP_ZONES.opportunity_zones", "cells": "CD_OPP_ZONE_CELLS.opportunity_zone_cells",
                    "label": "improvement opportunity"},
}
CACHE_S = 300  # scored zones and zone cells are cached briefly so panning the map stays quick
_plans: dict[str, dict] = {}  # zone_id -> plan (the DEM work is the slow part)
_cache: dict[tuple, tuple[float, object]] = {}


def kind_of(zone_id: str) -> str:
    if not ZONE_RE.match(zone_id or ""):
        raise HTTPException(400, "zone_id looks like Z00001 (gap zone) or O00001 (improvement opportunity)")
    return "opportunity" if zone_id.startswith("O") else "gap"


def _kind(kind: str) -> str:
    if kind not in KINDS:
        raise HTTPException(400, "type must be gap or opportunity")
    return kind


def _q(sql: str, log: list) -> list[dict]:
    try:
        rows = query(sql)
    except DBError as e:
        raise HTTPException(502, f"Query failed: {e}") from e
    finally:
        log.append({k: QUERY_LOG[-1][k] for k in ("sql", "duration_ms", "rows", "error")})
    return rows


def _cached(key: tuple, make):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_S:
        return hit[1]
    value = make()
    _cache[key] = (time.time(), value)
    return value


def scope_filter(scope: str, table: str = "") -> str:
    """SQL condition for a validated scope (the regex keeps values safe to inline)."""
    if not SCOPE_RE.match(scope):
        raise HTTPException(400, "scope must be uk, nation:<name> or la:<local authority code>")
    if scope == "uk":
        return "1 = 1"
    p = f"{table}." if table else ""
    kind, _, value = scope.partition(":")
    return f"{p}nation = '{value}'" if kind == "nation" else f"list_contains(string_split({p}la_codes, ','), '{value}')"


def _zones(scope: str, log: list, kind: str = "gap") -> pd.DataFrame:
    rows = _q(f"SELECT * FROM {KINDS[kind]['zones']} WHERE {scope_filter(scope)}", log)
    return pd.DataFrame(rows)


def _clean(v):
    return None if isinstance(v, float) and math.isnan(v) else v


def _bool(v) -> bool:
    return str(v).lower() == "true" if not isinstance(v, bool) else v


def _zone_row(r: dict, cells: list[str] | None = None, kind: str = "gap") -> dict:
    las = list(zip(str(r["la_codes"] or "").split(","), str(r["la_names"] or "").split("|")))
    out = {
        "zone_type": kind, "rank": r.get("rank"), "zone_id": r["zone_id"], "name": r["name"],
        "place_type": _clean(r.get("place_type")),
        "nation": r["nation"], "council": r["primary_la_name"], "council_code": r["primary_la_code"],
        "councils": [{"code": c, "name": n} for c, n in las if c],
        "people": round(r["population"]), "cells": r["n_cells"],
        "centroid": {"lat": r["centroid_lat"], "lon": r["centroid_lon"]},
        "median_speed_mbps": _clean(r.get("median_cell_mbps")), "download_mbps": _clean(r.get("download_mbps")),
        "tests": r["tests"], "confidence": scoring.confidence(int(r["tests"] or 0)),
        "trend_pct": _clean(r.get("trend_pct")), "quarters_with_data": r.get("quarters_with_data"),
        "operators_mean": round(r["operators_mean"], 2), "operators_nearby": [o for o in str(r["operators_nearby"] or "").split(",") if o],
        "score": r.get("score"), "breakdown": r.get("breakdown"),
        "links": {"explore": f"/?zone={r['zone_id']}&lat={r['centroid_lat']:.5f}&lon={r['centroid_lon']:.5f}&zoom=10.5",
                  "signal": f"/signal?lat={r['centroid_lat']:.5f}&lon={r['centroid_lon']:.5f}"},
    }
    if kind == "opportunity":
        out.update(people_by_reason={k: round(r[f"pop_{k}"]) for k in REASONS},
                   reasons=[k for k in str(r["reasons"] or "").split(",") if k],
                   shortfall_pct=_clean(r.get("shortfall_pct")), ref_mbps=_clean(r.get("ref_mbps")),
                   uk_median_mbps=_clean(r.get("uk_median_mbps")), limited_data=_bool(r.get("limited_data")))
    else:
        out.update(people_by_reason={"slow": round(r["pop_slow"]), "no_site": round(r["pop_no_site"]),
                                     "blocked": round(r["pop_blocked"])},
                   persistence=_clean(r.get("persistence")))
    if cells is not None:
        out["cell_ids"] = cells
    return out


def ranked(scope: str, weights_text: str | None, log: list, kind: str = "gap") -> tuple[pd.DataFrame, dict]:
    try:
        weights = scoring.parse_weights(weights_text, kind)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

    def make():
        return scoring.score(_zones(scope, log, kind), weights, kind)
    return _cached(("ranked", kind, scope, weights_text or ""), make)


def zone_cell_ids(kind: str, log: list) -> dict[str, list[str]]:
    """zone_id -> cell ids, for every zone of a kind (cached)."""
    def make():
        by_zone: dict[str, list[str]] = {}
        for r in _q(f"SELECT zone_id, cell_id FROM {KINDS[kind]['cells']}", log):
            by_zone.setdefault(r["zone_id"], []).append(r["cell_id"])
        return by_zone
    return _cached(("cells", kind), make)


# ------------------------------------------------------------------ what's on the map


def view_caps(zoom: float) -> tuple[int, int]:
    """(max gap zones, max opportunities) listed at this zoom (config.COUNCIL_VIEW)."""
    caps = (0, 0)
    for min_zoom, gaps, opps in config.COUNCIL_VIEW:
        if zoom >= min_zoom:
            caps = (gaps, opps)
    return caps


def in_bbox(df: pd.DataFrame, bbox: tuple[float, float, float, float] | None) -> pd.DataFrame:
    """Zones whose centroid lies in (min_lon, min_lat, max_lon, max_lat)."""
    if bbox is None or df.empty or "centroid_lon" not in df:
        return df
    w, s, e, n = bbox
    return df[(df.centroid_lon >= w) & (df.centroid_lon <= e) & (df.centroid_lat >= s) & (df.centroid_lat <= n)]


def select_view(scored: pd.DataFrame, bbox, cap: int, selected: str | None = None) -> tuple[pd.DataFrame, int]:
    """The top `cap` zones (by score) in the viewport; a selected zone is always kept. Returns (shown, in view)."""
    if scored.empty or "zone_id" not in scored:  # no zones of this kind in the scope
        return scored, 0
    view = in_bbox(scored, bbox)
    shown = view.head(cap) if cap > 0 else view.head(0)
    if selected and selected in set(scored.zone_id) and selected not in set(shown.zone_id):
        shown = pd.concat([shown, scored[scored.zone_id == selected]])
    return shown, len(view)


def _parse_bbox(bbox: str | None):
    if not bbox:
        return None
    try:
        w, s, e, n = (float(x) for x in bbox.split(","))
    except ValueError as err:
        raise HTTPException(400, "bbox is minLon,minLat,maxLon,maxLat") from err
    return (w, s, e, n)


def view_note(kind_label: str, shown: int, in_view: int, cap: int) -> str | None:
    if in_view > cap > 0 and shown < in_view:
        return f"Showing the top {cap} of {in_view:,} {kind_label} in view. Zoom in to see more."
    return None


@router.get("/view")
def view(bbox: str | None = None, zoom: float = Query(5.0, ge=0, le=24), scope: str = "uk",
         show: str = Query("both", pattern="^(gaps|opportunities|both)$"), weights: str | None = None,
         opp_weights: str | None = None, selected: str | None = None) -> dict:
    """The zones listed for the current map view. The map and the list both draw from this response."""
    log: list = []
    t = time.time()
    box = _parse_bbox(bbox)
    max_gaps, max_opps = view_caps(zoom)
    out = {"zoom": zoom, "scope": scope, "show": show, "caps": {"gaps": max_gaps, "opportunities": max_opps}, "notes": []}
    for kind, key, cap, w in [("gap", "gaps", max_gaps, weights), ("opportunity", "opportunities", max_opps, opp_weights)]:
        wanted = show in ("both", key)
        scored, summary = ranked(scope, w, log, kind)
        if not wanted:
            out[key] = {"in_view": 0, "shown": 0, "zones": [], "hidden": "not selected"}
            continue
        shown, n_view = select_view(scored, box, cap, selected)
        cells = zone_cell_ids(kind, log)
        rows = [_zone_row(r, cells.get(r["zone_id"], []), kind) for r in shown.to_dict("records")]
        out[key] = {"in_view": n_view, "shown": len(rows), "zones": rows, "scoring": summary,
                    "people_in_view": round(float(in_bbox(scored, box).population.sum())) if n_view else 0}
        if cap == 0 and n_view:
            out[key]["hidden"] = "zoom"
            out["notes"].append(f"{n_view:,} improvement opportunities in view: zoom in to see them.")
        elif (note := view_note("gap zones" if kind == "gap" else "improvement opportunities", len(rows), n_view, cap)):
            out["notes"].append(note)
    out["duration_ms"] = round((time.time() - t) * 1000)
    out["queries"] = log
    return out


# ------------------------------------------------------------------ area picker


@router.get("/scopes")
@lru_cache(maxsize=1)  # the list only changes when the data is rebuilt
def scopes() -> dict:
    log: list = []
    las = _q("""SELECT a.laua_code, a.laua_name, c.nation, sum(c.population) AS population,
  min(c.lat) AS min_lat, max(c.lat) AS max_lat, min(c.lon) AS min_lon, max(c.lon) AS max_lon
FROM CD_OFCOM_AREA.ofcom_area_r7 a JOIN CD_CELLS.cells_r7 c ON c.cell_id = a.cell_id
WHERE a.laua_code IS NOT NULL GROUP BY a.laua_code, a.laua_name, c.nation""", log)
    zones = {k: _q(f"SELECT zone_id, nation, la_codes, population FROM {KINDS[k]['zones']}", log) for k in KINDS}
    return {**picker_counts(las, zones["gap"], zones["opportunity"]), "queries": log}


def picker_counts(las: list[dict], gaps: list[dict], opps: list[dict]) -> dict:
    """Counts for the area picker: per council and nation, gap zones with people affected, and opportunities."""
    def per_la(zones):
        n: dict[str, int] = {}
        people: dict[str, float] = {}
        for z in zones:
            for code in {c for c in str(z["la_codes"] or "").split(",") if c}:
                n[code] = n.get(code, 0) + 1
                people[code] = people.get(code, 0.0) + float(z["population"])
        return n, people
    g_n, g_people = per_la(gaps)
    o_n, o_people = per_la(opps)
    # A council straddling a border is listed under the nation holding most of its people.
    best: dict[str, dict] = {}
    for r in las:
        if r["laua_code"] not in best or r["population"] > best[r["laua_code"]]["population"]:
            best[r["laua_code"]] = r
    bounds: dict[str, list[float]] = {}
    for r in las:  # bounds over every part of the council
        b = bounds.setdefault(r["laua_code"], [r["min_lon"], r["min_lat"], r["max_lon"], r["max_lat"]])
        bounds[r["laua_code"]] = [min(b[0], r["min_lon"]), min(b[1], r["min_lat"]), max(b[2], r["max_lon"]), max(b[3], r["max_lat"])]
    councils = sorted(({"scope": f"la:{r['laua_code']}", "code": r["laua_code"], "name": r["laua_name"], "nation": r["nation"],
                        "zones": g_n.get(r["laua_code"], 0), "people": round(g_people.get(r["laua_code"], 0)),
                        "opportunities": o_n.get(r["laua_code"], 0), "opportunity_people": round(o_people.get(r["laua_code"], 0)),
                        "bbox": [round(x, 4) for x in bounds[r["laua_code"]]]} for r in best.values()),
                      key=lambda x: (x["nation"], x["name"]))
    nations = [{"scope": f"nation:{n}", "name": n, "zones": sum(1 for z in gaps if z["nation"] == n),
                "people": round(sum(float(z["population"]) for z in gaps if z["nation"] == n)),
                "opportunities": sum(1 for z in opps if z["nation"] == n)} for n in NATIONS]
    return {"uk": {"scope": "uk", "name": "United Kingdom", "zones": len(gaps),
                   "people": round(sum(float(z["population"]) for z in gaps)), "opportunities": len(opps)},
            "nations": nations, "councils": councils}


# ------------------------------------------------------------------ ranked lists


@router.get("/zones")
def zones(scope: str = "uk", weights: str | None = None, limit: int = Query(500, ge=1, le=2000),
          type: str = Query("gap", pattern="^(gap|opportunity)$")) -> dict:
    log: list = []
    t = time.time()
    kind = _kind(type)
    scored, summary = ranked(scope, weights, log, kind)
    cells = zone_cell_ids(kind, log)
    rows = [_zone_row(r, cells.get(r["zone_id"], []), kind) for r in scored.head(limit).to_dict("records")]
    return {"scope": scope, "type": kind, "zones_in_scope": len(scored),
            "people_in_zones": round(float(scored.population.sum())) if len(scored) else 0,
            "scoring": summary, "zones": rows, "duration_ms": round((time.time() - t) * 1000), "queries": log}


@router.get("/zones.csv")
def zones_csv(scope: str = "uk", weights: str | None = None, type: str = Query("gap", pattern="^(gap|opportunity)$")) -> Response:
    log: list = []
    kind = _kind(type)
    scored, summary = ranked(scope, weights, log, kind)
    buf = io.StringIO()
    w = csv.writer(buf)
    factors = summary["factors_used"]
    extra = ["reasons", "shortfall_pct", "limited_data"] if kind == "opportunity" else ["persistence"]
    w.writerow(["rank", "zone_id", "zone_type", "name", "council", "nation", "people", "cells", "median_speed_mbps", "tests",
                "confidence", *extra, "trend_pct", "operators_mean", "operators_nearby", "score",
                *[f"{f}_contribution" for f in factors], "centroid_lat", "centroid_lon"])
    for r in scored.to_dict("records"):
        z = _zone_row(r, None, kind)
        ex = [" ".join(z["reasons"]), z["shortfall_pct"], z["limited_data"]] if kind == "opportunity" else [z["persistence"]]
        w.writerow([z["rank"], z["zone_id"], kind, z["name"], z["council"], z["nation"], z["people"], z["cells"],
                    z["median_speed_mbps"], z["tests"], z["confidence"], *ex, z["trend_pct"],
                    z["operators_mean"], " ".join(z["operators_nearby"]), z["score"],
                    *[r["breakdown"][f]["contribution"] for f in factors], round(r["centroid_lat"], 5), round(r["centroid_lon"], 5)])
    name = scope.replace(":", "_").replace(" ", "_")
    prefix = "opportunities" if kind == "opportunity" else "gap_zones"
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{prefix}_{name}.csv"'})


# ------------------------------------------------------------------ one zone


def _summary(z: dict, cells: list[dict]) -> list[str]:
    """Plain sentences computed from the zone's numbers."""
    r = z["people_by_reason"]
    out = [f"{z['people']:,} people in {z['cells']} H3 cells ({z['council']}, {z['nation']})."]
    if z["zone_type"] == "opportunity":
        out[0] = out[0][:-1] + "; an improvement opportunity: not poorly served, but with a measurable weakness."
        parts = []
        if r["slow"]:
            parts.append(f"{r['slow']:,} in cells measured below half the local reference median "
                         f"({z['ref_mbps']:.0f} Mbps, the lower of the area and UK medians)")
        if r["falling"]:
            parts.append(f"{r['falling']:,} in cells whose speeds fell by 30% or more over the last quarters")
        if r["few_operators"]:
            parts.append(f"{r['few_operators']:,} in cells where only 1-2 operators are identified within 5 km")
        if r["capacity"]:
            parts.append(f"{r['capacity']:,} in busy cells with a capacity-strain signal")
        if parts:
            out.append("Weaknesses: " + "; ".join(parts) + ".")
        if z.get("shortfall_pct") is not None:
            out.append(f"Measured cells are on average {z['shortfall_pct']:.0f}% below their reference median.")
    else:
        parts = []
        if r["no_site"]:
            parts.append(f"{r['no_site']:,} with no identified site within 5 km")
        if r["blocked"]:
            parts.append(f"{r['blocked']:,} where terrain blocks the line to every site within 5 km")
        if r["slow"]:
            parts.append(f"{r['slow']:,} measured below 5 Mbps")
        if parts:
            out.append("Poorly served: " + "; ".join(parts) + ".")
    if z["tests"]:
        speed = f" averaging {z['download_mbps']:.1f} Mbps" if z["download_mbps"] is not None else ""
        out.append(f"{z['tests']} Ookla test{'s' if z['tests'] != 1 else ''} in the latest quarter{speed} "
                   f"(data confidence: {z['confidence']}).")
    else:
        out.append("No Ookla tests in the latest quarter (data confidence: low).")
    if z.get("persistence") is not None:
        out.append(f"Below 5 Mbps in {z['persistence']:.0%} of the {z['quarters_with_data']} quarters with tests.")
    if z["trend_pct"] is not None:
        out.append(f"Speed {'fell' if z['trend_pct'] < 0 else 'rose'} {abs(z['trend_pct']):.0f}% from the oldest to the latest quarter.")
    ops = z["operators_nearby"]
    out.append(f"On average {z['operators_mean']:.1f} operators have approximate sites within 5 km of its cells"
               + (f" ({', '.join(ops)} somewhere nearby)." if ops else "; none identified."))
    return out


@router.get("/zones/{zone_id}/cells")
def zone_cells(zone_id: str) -> dict:
    kind = kind_of(zone_id)
    log: list = []
    rows = _q(f"""SELECT c.cell_id, z.name FROM {KINDS[kind]['cells']} c
JOIN {KINDS[kind]['zones']} z ON z.zone_id = c.zone_id WHERE c.zone_id = '{zone_id}'""", log)
    if not rows:
        raise HTTPException(404, f"No zone {zone_id}")
    return {"zone_id": zone_id, "zone_type": kind, "name": rows[0]["name"], "cell_ids": [r["cell_id"] for r in rows],
            "queries": log}


def _km(lat1, lon1, lat2, lon2) -> float:
    return 6371.0 * 2 * math.asin(math.sqrt(math.sin(math.radians(lat2 - lat1) / 2) ** 2 + math.cos(math.radians(lat1))
                                            * math.cos(math.radians(lat2)) * math.sin(math.radians(lon2 - lon1) / 2) ** 2))


@router.get("/zones/{zone_id}")
def zone_detail(zone_id: str, weights: str | None = None) -> dict:
    kind = kind_of(zone_id)
    t_ = KINDS[kind]
    log: list = []
    t = time.time()
    rows = _q(f"SELECT * FROM {t_['zones']} WHERE zone_id = '{zone_id}'", log)
    if not rows:
        raise HTTPException(404, f"No zone {zone_id}")
    z = rows[0]
    # Rank and breakdown within the zone's main council and the UK.
    ranks = {}
    for scope in ["uk", f"la:{z['primary_la_code']}"]:
        scored, _ = ranked(scope, weights, log, kind)
        hit = scored[scored.zone_id == zone_id]
        if len(hit):
            ranks[scope] = {"rank": int(hit["rank"].iloc[0]), "of": len(scored), "score": float(hit["score"].iloc[0]),
                            "breakdown": hit["breakdown"].iloc[0]}
    cells = _q(f"SELECT * FROM {t_['cells']} WHERE zone_id = '{zone_id}'", log)
    flags = REASONS if kind == "opportunity" else ("slow", "no_site", "blocked")
    for c in cells:
        for k in flags:
            c[k] = _bool(c[k])
    lats, lons = [c["lat"] for c in cells], [c["lon"] for c in cells]
    pad_lat = (siting.BUFFER_KM + siting.REACH_KM + 2) / 111.0
    pad_lon = pad_lat / math.cos(math.radians(sum(lats) / len(lats)))
    box = (min(lats) - pad_lat, max(lats) + pad_lat, min(lons) - pad_lon, max(lons) + pad_lon)
    area = _q(f"""SELECT c.cell_id, c.lat, c.lon, c.population, g.zone_id
FROM CD_CELLS.cells_r7 c LEFT JOIN CD_GAP_ZONE_CELLS.gap_zone_cells g ON g.cell_id = c.cell_id
WHERE c.population >= 10 AND c.lat BETWEEN {box[0]:.5f} AND {box[1]:.5f} AND c.lon BETWEEN {box[2]:.5f} AND {box[3]:.5f}""", log)
    for a in area:
        a["poor"] = a.get("zone_id") is not None
    pad = siting.EXISTING_KM / 111.0
    sites = _q(f"""SELECT site_id, lat, lon, operators, operators_confirmed, sources, antenna_height_m
FROM CD_MASTS_SITES.masts_sites
WHERE lat BETWEEN {min(lats) - pad:.5f} AND {max(lats) + pad:.5f}
  AND lon BETWEEN {min(lons) - pad / math.cos(math.radians(lats[0])):.5f} AND {max(lons) + pad / math.cos(math.radians(lats[0])):.5f}""", log)
    zone = _zone_row({**z, **(ranks.get("uk") or {})}, [c["cell_id"] for c in cells], kind)
    planning_t = time.time()
    if zone_id not in _plans:
        _plans[zone_id] = siting.plan(z, cells, area, sites, kind=kind)
    plan = _plans[zone_id]
    # For the candidate cards: distance from each candidate to the nearest existing site we know of.
    for c in plan["candidates"]:
        c["nearest_existing_km"] = round(min((_km(c["lat"], c["lon"], s["lat"], s["lon"]) for s in sites), default=np.nan), 2) \
            if sites else None
    cell_keys = ("cell_id", "population", "ring_mbps", "ring_tests", "lat", "lon", *flags) + (("severity",) if kind == "gap" else ("ref_mbps",))
    return {"zone": zone, "zone_type": kind, "ranks": ranks, "summary": _summary(zone, cells),
            "reason_labels": REASON_LABELS if kind == "opportunity" else None,
            "cells": [{k: c.get(k) for k in cell_keys} for c in cells],
            "existing_all": [{"site_id": s["site_id"], "lat": s["lat"], "lon": s["lon"],
                              "operators": [o for o in str(s.get("operators") or "").split(",") if o]} for s in sites[:400]],
            **plan, "planning_ms": round((time.time() - planning_t) * 1000),
            "duration_ms": round((time.time() - t) * 1000), "queries": log}
