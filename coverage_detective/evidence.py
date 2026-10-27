"""Evidence bundle for one point: what the Investigator agent will call.

    build_bundle(query="WC2N 5DU")  or  build_bundle(lat=54.445, lon=-3.105)

1. Resolve the point (postcodes.io): its H3 r7 cell and the six neighbours.
2. Zetaris (every query is logged in the bundle):
     Q1  the cross-source cell query (cells, Ookla + history + neighbours, masts, Ofcom area,
         terrain, line of sight), the same one the explorer uses;
     Q2  approximate mast sites within 10 km of the exact point (CD_MASTS_SITES);
     Q3  UK context: median and 90th percentile devices per head and download speed
         (cached for the process, since it only changes when the data is rebuilt);
     Q4  Ookla tiles (~350-400 m) within 2 km of the exact point, latest quarter (the speed
         figure uses those within 1 km; the rest are for the map).
3. Line of sight from the exact point to the nearest site of each operator within 10 km
   (and to the nearest site of any operator), over the Copernicus DEM.
4. Live: weather, Met Office warnings, EA flood warnings.

The bundle also carries `operators` (per operator: nearest site, confirmed or inferred, line
of sight from the exact point) and `likely_best` (a rule-based summary, not reviewed by
agents). Every item: id, source, data_period, as_of, query_or_url, cell_ids, finding (one plain
sentence computed here, following the CLAUDE.md wording rules), value, sample_size,
strength (strong / moderate / weak) and bears_on (causes it is relevant to). Numbers come
from code and SQL; nothing here is written by an LLM.
"""

from __future__ import annotations

import json
import math
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from zoneinfo import ZoneInfo

import h3

from coverage_detective.config import H3_RES, SAMPLE_LOCATIONS, sample_mode
from coverage_detective.data import manifest, terrain
from coverage_detective.data.masts import bearing_deg
from coverage_detective.data.queries import cell_sql
from coverage_detective.data.zetaris import QUERY_LOG, ZetarisError, query
from coverage_detective.sources import flood_warnings, geocode, live, met_office_warnings, weather

OPERATORS = ["EE", "O2", "Three", "Vodafone"]
SITE_RADIUS_KM = 10.0
OPERATOR_RADIUS_KM = 5.0
STRONG_TESTS, WEAK_TESTS = 50, 10
LOS_STRONG_M = 30.0   # blocked by this much: hard to explain away by trees or buildings
LOS_MARGINAL_M = 10.0
SPEED_RADIUS_KM = 1.0
TILE_MAP_RADIUS_KM = 2.0
MAP_SITES_MAX = 2000  # sites within 10 km sent for the map (nearest first)
UK_TZ = ZoneInfo("Europe/London")
COMPASS = ["north", "north-east", "east", "south-east", "south", "south-west", "west", "north-west"]

LIMITATIONS = [
    "Causes are inferred from public data, not confirmed: operators' network data is not public.",
    "Ookla speeds are quarterly aggregates with no time-of-day detail; congestion can only be inferred.",
    "No address-level Ofcom coverage; Ofcom figures are for the whole constituency and are predictions.",
    "Mast sites are approximate (crowd-sourced OpenCelliD, OpenStreetMap, Ofcom's licence register) and "
    "incomplete, so 'no sites found' is weak evidence.",
    "Line of sight uses a surface model (trees and buildings included) and mostly assumed antenna heights; "
    "blocked means a weaker signal, not no signal.",
]


class Bundle:
    def __init__(self):
        self.items: list[dict] = []
        self.queries: list[dict] = []
        self.errors: list[str] = []

    def add(self, id: str, source: str, meta: dict, query_or_url: str, cell_ids: list[str], finding: str,
            value, sample_size, strength: str, bears_on: list[str]) -> None:
        self.items.append({
            "id": id, "source": source, "data_period": meta.get("data_period"), "as_of": meta.get("as_of"),
            "query_or_url": query_or_url, "cell_ids": cell_ids, "finding": finding, "value": value,
            "sample_size": sample_size, "strength": strength, "bears_on": bears_on,
        })

    def zetaris(self, qid: str, sql: str, cached: bool = False) -> list[dict]:
        if cached and qid in _CACHE:
            rows, entry = _CACHE[qid]
            self.queries.append({"id": qid, **entry, "cached": True})
            return rows
        try:
            rows = query(sql, timeout=60)
        finally:
            self.queries.append({"id": qid, **QUERY_LOG[-1], "cached": False})
        if cached:
            _CACHE[qid] = (rows, dict(QUERY_LOG[-1]))
        return rows


_CACHE: dict[str, tuple[list[dict], dict]] = {}


# --- small helpers ---------------------------------------------------------------------------

def _meta(table: str) -> dict:
    try:
        return manifest.evidence_meta(table)
    except KeyError:
        return {"data_period": None, "as_of": None}


def _bool(v) -> bool:
    return v is True or str(v).lower() == "true"


def _ops(v) -> list[str]:
    return [o for o in str(v or "").split(",") if o]


def _num(v, d=1) -> str:
    return f"{v:,.{d}f}" if v is not None else "unknown"


def _km(m: float) -> str:
    return f"{m / 1000:.1f} km" if m >= 1000 else f"{m:.0f} m"


def _compass(deg: float) -> str:
    return COMPASS[int((deg % 360) / 45 + 0.5) % 8]


def _join(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _uk_time(iso: str | None) -> str:
    if not iso:
        return "unknown time"
    dt = datetime.fromisoformat(iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UK_TZ)
    return dt.astimezone(UK_TZ).strftime("%a %d %b %H:%M")


def _month(date: str | None) -> str:
    try:
        return datetime.strptime(str(date)[:7], "%Y-%m").strftime("%B %Y")
    except ValueError:
        return str(date)


def _live_strength(res: live.LiveResult, normal: str) -> str:
    return normal if res.status in ("live", "cached") else "weak"


def _site_label(site: dict) -> str:
    src = {"opencellid": "OpenCelliD", "osm": "OpenStreetMap", "ofcom_wtr": "Ofcom licence register"}
    return " + ".join(src.get(s, s) for s in str(site["sources"]).split(","))


# --- SQL -------------------------------------------------------------------------------------

def sites_sql(lat: float, lon: float, km: float = SITE_RADIUS_KM) -> str:
    dlat = km / 111.0
    dlon = km / (111.32 * math.cos(math.radians(lat)))
    return f"""SELECT site_id, lat, lon, sources, operators, operators_inferred, operators_confirmed, antenna_height_m,
  has_microwave_backhaul, cell_count, distance_km
FROM (
  SELECT s.*, 2 * 6371 * asin(sqrt(pow(sin(radians(s.lat - {lat}) / 2), 2) + cos(radians({lat})) * cos(radians(s.lat))
         * pow(sin(radians(s.lon - ({lon})) / 2), 2))) AS distance_km
  FROM CD_MASTS_SITES.masts_sites s
  WHERE s.lat BETWEEN {lat - dlat:.5f} AND {lat + dlat:.5f} AND s.lon BETWEEN {lon - dlon:.5f} AND {lon + dlon:.5f}
) x
WHERE distance_km <= {km}
ORDER BY distance_km"""


UK_CONTEXT_SQL = """SELECT percentile_approx(devices_per_1k_pop, 0.5) AS devices_per_1k_p50,
  percentile_approx(devices_per_1k_pop, 0.9) AS devices_per_1k_p90,
  percentile_approx(download_mbps, 0.5) AS download_mbps_p50, count(*) AS cells
FROM CD_OOKLA_MOBILE.ookla_mobile_r7 WHERE tests >= 10"""


def tiles_sql(lat: float, lon: float, quarter: str, km: float = SPEED_RADIUS_KM) -> str:
    dlat = km / 111.0
    dlon = km / (111.32 * math.cos(math.radians(lat)))
    return f"""SELECT quadkey, lat, lon, download_mbps, upload_mbps, latency_ms, tests, devices, distance_km
FROM (
  SELECT t.*, 2 * 6371 * asin(sqrt(pow(sin(radians(t.lat - {lat}) / 2), 2) + cos(radians({lat})) * cos(radians(t.lat))
         * pow(sin(radians(t.lon - ({lon})) / 2), 2))) AS distance_km
  FROM CD_OOKLA_MOBILE_TILES.ookla_mobile_tiles t
  WHERE t.quarter = '{quarter}'
    AND t.lat BETWEEN {lat - dlat:.5f} AND {lat + dlat:.5f} AND t.lon BETWEEN {lon - dlon:.5f} AND {lon + dlon:.5f}
) x
WHERE distance_km <= {km}
ORDER BY distance_km"""


# --- evidence from the stored tables (Zetaris) -----------------------------------------------

def nearby_speed_item(b: Bundle, map_tiles: list[dict], quarter: str, cell_id: str) -> None:
    """Test-weighted speed over the Ookla tiles within 1 km of the exact point (all 2 km tiles go to the map)."""
    meta = _meta("ookla_mobile_tiles")
    tiles = [t for t in map_tiles if t["distance_km"] <= SPEED_RADIUS_KM]
    map_value = [{k: t[k] for k in ("lat", "lon", "download_mbps", "tests", "distance_km")} for t in map_tiles]
    tests = sum(t["tests"] for t in tiles)
    if not tests:
        b.add("speed_1km", "Ookla open data tiles (Zetaris CD_OOKLA_MOBILE_TILES)", meta, "zetaris:Q4", [cell_id],
              f"No Ookla speed tests were recorded within {SPEED_RADIUS_KM:.0f} km of this point in {quarter}.",
              {"tiles": map_value, "quarter": quarter, "radius_km": SPEED_RADIUS_KM}, {"tests": 0, "tiles": 0}, "weak",
              ["insufficient_data"])
        return
    mbps = sum(t["download_mbps"] * t["tests"] for t in tiles) / tests
    devices = sum(t["devices"] for t in tiles)
    strength = "strong" if tests >= STRONG_TESTS else "moderate" if tests >= WEAK_TESTS else "weak"
    few = " Few tests, so this is weak evidence." if tests < WEAK_TESTS else ""
    b.add("speed_1km", "Ookla open data tiles (Zetaris CD_OOKLA_MOBILE_TILES)", meta, "zetaris:Q4",
          sorted({cell_id, *(h3.latlng_to_cell(t["lat"], t["lon"], H3_RES) for t in tiles)}),
          f"Within {SPEED_RADIUS_KM:.0f} km of this point, mobile download speed averaged {mbps:.1f} Mbps in {quarter} "
          f"({tests} tests from {devices} devices on {len(tiles)} Ookla tiles of about 350-400 m).{few}",
          {"download_mbps": round(mbps, 1), "quarter": quarter, "radius_km": SPEED_RADIUS_KM,
           "tiles": map_value},
          {"tests": tests, "devices": devices, "tiles": len(tiles)}, strength,
          ["distance_to_mast", "terrain_blocking", "capacity_strain", "insufficient_data"])


def speed_items(b: Bundle, c: dict, cell_id: str, ring: list[str], ctx: dict | None) -> None:
    meta, q = _meta("ookla_mobile_r7"), "zetaris:Q1"
    tests, quarter = c.get("tests"), c.get("quarter") or _meta("ookla_mobile_r7")["data_period"]
    if tests:
        strength = "strong" if tests >= STRONG_TESTS else "moderate" if tests >= WEAK_TESTS else "weak"
        few = f" Only {tests} test{'s' if tests != 1 else ''}, so this is weak evidence." if tests < WEAK_TESTS else ""
        uk = f" The UK median across cells with 10+ tests is {ctx['download_mbps_p50']:.0f} Mbps." if ctx else ""
        b.add("speed_cell", "Ookla open data (Zetaris CD_OOKLA_MOBILE)", meta, q, [cell_id],
              f"Mobile download speed in this cell averaged {c['download_mbps']:.1f} Mbps in {quarter} "
              f"({tests} tests from {c['devices']} devices; upload {_num(c.get('upload_mbps'))} Mbps, latency "
              f"{_num(c.get('latency_ms'), 0)} ms).{uk}{few}",
              {"download_mbps": c["download_mbps"], "upload_mbps": c.get("upload_mbps"),
               "latency_ms": c.get("latency_ms"), "quarter": quarter,
               "uk_median_download_mbps": ctx and ctx["download_mbps_p50"]},
              {"tests": tests, "devices": c.get("devices")}, strength,
              ["distance_to_mast", "terrain_blocking", "capacity_strain", "insufficient_data"])
    else:
        b.add("speed_cell", "Ookla open data (Zetaris CD_OOKLA_MOBILE)", meta, q, [cell_id],
              f"No Ookla speed tests were recorded in this cell in {quarter}, so there is no measured speed here.",
              None, {"tests": 0}, "weak", ["insufficient_data"])

    nt = c.get("neighbour_tests")
    if nt:
        n_cells = c.get("neighbour_cells_with_data")
        b.add("speed_neighbours", "Ookla open data (Zetaris CD_OOKLA_MOBILE)", meta, q, ring,
              f"The 6 neighbouring cells ({n_cells} with data) averaged {c['neighbour_download_mbps']:.1f} Mbps "
              f"from {nt} tests in {quarter}.",
              {"download_mbps": c["neighbour_download_mbps"], "cells_with_data": n_cells}, {"tests": nt},
              "moderate" if nt >= WEAK_TESTS else "weak", ["insufficient_data", "distance_to_mast"])
    else:
        b.add("speed_neighbours", "Ookla open data (Zetaris CD_OOKLA_MOBILE)", meta, q, ring,
              f"None of the 6 neighbouring cells had Ookla speed tests in {quarter}.", None, {"tests": 0}, "weak",
              ["insufficient_data"])

    quarters = int(c.get("quarters_with_data") or 0)
    if tests and quarters >= 2 and c.get("download_change_pct") is not None:
        pct = c["download_change_pct"]
        direction = "fell" if pct < 0 else "rose"
        weak = tests < WEAK_TESTS or quarters < 3
        b.add("speed_trend", "Ookla open data (Zetaris CD_OOKLA_MOBILE_HISTORY)", _meta("ookla_mobile_history_r7"), q,
              [cell_id],
              f"Download speed here {direction} {abs(pct):.0f}% over {quarters} quarters, from "
              f"{c['download_mbps_oldest']:.1f} to {c['download_mbps']:.1f} Mbps"
              + (" (few tests or quarters, so a weak signal)." if weak else "."),
              {"change_pct": pct, "from_mbps": c["download_mbps_oldest"], "to_mbps": c["download_mbps"],
               "history": c.get("history")}, {"quarters": quarters, "tests_latest": tests},
              "weak" if weak else "moderate", ["capacity_strain"])

    per_head = c.get("devices_per_1k_pop")
    if tests and per_head is not None and ctx:
        high = per_head >= ctx["devices_per_1k_p90"]
        b.add("usage_per_head", "Ookla open data (Zetaris CD_OOKLA_MOBILE)", meta, "zetaris:Q1, zetaris:Q3", [cell_id],
              f"{per_head:.1f} devices per 1,000 residents ran speed tests here in {quarter}, against a UK median of "
              f"{ctx['devices_per_1k_p50']:.1f} (90th percentile {ctx['devices_per_1k_p90']:.1f})"
              + (": unusually high, which can point to visitors or heavy use." if high else "."),
              {"devices_per_1k_pop": per_head, "uk_p50": ctx["devices_per_1k_p50"], "uk_p90": ctx["devices_per_1k_p90"],
               "above_uk_p90": high},
              {"devices": c.get("devices"), "population": c.get("population")},
              "weak" if tests < WEAK_TESTS else "moderate", ["capacity_strain"])


def ofcom_item(b: Bundle, c: dict, cell_id: str) -> None:
    if not c.get("pcon_name"):
        return
    when = _month(c.get("ofcom_data_date"))
    b.add("ofcom_area", "Ofcom Connected Nations (Zetaris CD_OFCOM_AREA)", _meta("ofcom_area_r7"), "zetaris:Q1",
          [cell_id],
          f"In {c['pcon_name']}, {c['g4_geo_out_0']:.1f}% of land has no outdoor 4G from any operator and "
          f"{c['g4_geo_out_1']:.1f}% from only one (Ofcom Connected Nations, {when}). This is a predicted figure "
          f"for the whole constituency, not this exact location.",
          {"area": c["pcon_name"], "g4_geo_out_0": c["g4_geo_out_0"], "g4_geo_out_1": c["g4_geo_out_1"],
           "g4_prem_in_0": c.get("g4_prem_in_0"), "local_authority": c.get("laua_name"),
           "laua_g4_geo_out_0": c.get("laua_g4_geo_out_0")},
          None, "moderate", ["distance_to_mast", "single_operator"])


def terrain_item(b: Bundle, c: dict, cell_id: str) -> None:
    if c.get("elevation_range_m") is None:
        return
    rng = c["elevation_range_m"]
    hilly = "hilly ground, where terrain can block signal" if rng >= 150 else \
        "rolling ground" if rng >= 50 else "fairly flat ground"
    b.add("terrain_cell", "Copernicus DEM GLO-30 (Zetaris CD_TERRAIN)", _meta("terrain_r7"), "zetaris:Q1", [cell_id],
          f"Ground in this cell ranges over {rng:.0f} m (mean elevation {c['elevation_mean_m']:.0f} m): {hilly}.",
          {"elevation_range_m": rng, "elevation_mean_m": c["elevation_mean_m"]}, {"dem_samples": 343}, "moderate",
          ["terrain_blocking"])


# --- mast sites around the exact point -------------------------------------------------------

def site_items(b: Bundle, sites: list[dict], lat: float, lon: float, cell_id: str) -> None:
    meta, q = _meta("masts_sites"), "zetaris:Q2"
    src = "Approximate mast sites (Zetaris CD_MASTS_SITES)"
    if not sites:
        b.add("nearest_site", src, meta, q, [cell_id],
              f"No approximate mast sites were found within {SITE_RADIUS_KM:.0f} km. This is weak evidence: "
              "OpenCelliD's free data is incomplete.", None, {"sites": 0}, "weak", ["distance_to_mast", "insufficient_data"])
        return
    s = sites[0]
    ops, confirmed = _ops(s["operators"]), _ops(s["operators_confirmed"])
    who = (f"operators {_join(ops)}" if len(ops) > 1 else f"operator {ops[0]}") if ops else "operator unknown"
    how = ", confirmed by Ofcom's licence register" if confirmed else ", inferred from crowd-sourced data" if ops else ""
    b.add("nearest_site", src, meta, q, [s.get("cell_id") or cell_id],
          f"The nearest approximate mast site is {s['distance_km']:.1f} km {_compass(bearing_deg(lat, lon, s['lat'], s['lon']))} "
          f"({_site_label(s)}; {who}{how}).",
          {"site_id": s["site_id"], "distance_km": round(s["distance_km"], 2),
           "bearing_deg": round(bearing_deg(lat, lon, s["lat"], s["lon"]), 0), "operators": ops,
           "operators_confirmed": confirmed, "sources": s["sources"]},
          {"sites_within_10km": len(sites)}, "moderate" if s["distance_km"] <= 5 else "weak", ["distance_to_mast"])

    near = [x for x in sites if x["distance_km"] <= OPERATOR_RADIUS_KM]
    named = sorted({o for x in near for o in _ops(x["operators"])})
    conf = sorted({o for x in near for o in _ops(x["operators_confirmed"])})
    unknown = sum(1 for x in near if not _ops(x["operators"]))
    if not near:
        finding = (f"No approximate mast sites were found within {OPERATOR_RADIUS_KM:.0f} km (weak evidence: "
                   "OpenCelliD's free data is incomplete).")
    elif len(named) == 4:
        finding = f"All four operators have approximate mast sites within {OPERATOR_RADIUS_KM:.0f} km."
    elif named:
        finding = (f"Only {len(named)} operator{'s' if len(named) > 1 else ''} ({_join(named)}) "
                   f"{'have' if len(named) > 1 else 'has'} approximate mast sites within {OPERATOR_RADIUS_KM:.0f} km.")
    else:
        finding = f"{len(near)} approximate mast sites lie within {OPERATOR_RADIUS_KM:.0f} km, but their operators are not known."
    if near and conf:
        inferred = [o for o in named if o not in conf]
        finding += f" {_join(conf)} confirmed by Ofcom's licence register" + (
            f"; {_join(inferred)} inferred from crowd-sourced data." if inferred else ".")
    elif named:
        finding += " Operators are inferred from crowd-sourced data."
    if unknown:
        finding += f" Operators could not be identified for {unknown} nearby site{'s' if unknown > 1 else ''}."
    status = ("no_sites_found" if not near else "confirmed" if conf else "inferred" if named else "unknown")
    # Unknown-operator sites can only add operators, so they weaken the item only when fewer than four are named.
    if len(named) == 4:
        strength = "strong" if len(conf) == 4 else "moderate"
    else:
        strength = "weak" if (not near or len(near) < 3 or unknown) else "moderate"
    b.add("operators_5km", src, meta, q, [cell_id], finding,
          {"operators": named, "operators_confirmed": conf, "operator_data_status": status},
          {"sites_within_5km": len(near), "sites_unknown_operator": unknown}, strength,
          ["single_operator", "distance_to_mast"])


def los_class(los: dict) -> str:
    """clear / marginal (blocked by < 10 m: may be trees or buildings) / blocked."""
    if not los["blocked"]:
        return "clear"
    return "marginal" if los["max_obstruction_m"] < LOS_MARGINAL_M else "blocked"


def no_site_item(b: Bundle, op: str, sites: list[dict], cell_id: str) -> None:
    b.add(f"no_site_{op.lower()}", "Approximate mast sites (Zetaris CD_MASTS_SITES)", _meta("masts_sites"),
          "zetaris:Q2", [cell_id],
          f"No approximate {op} mast site was found within {SITE_RADIUS_KM:.0f} km. This is weak evidence: "
          "crowd-sourced data is incomplete and many sites have unknown operators.",
          None, {"sites_within_10km": len(sites)}, "weak", ["distance_to_mast", "single_operator"])


def los_item(b: Bundle, s: dict, for_ops: list[str], lat: float, lon: float, cell_id: str,
             los: dict | None = None, item_id: str | None = None) -> dict:
    """Line of sight from the point to one site, as an evidence item; returns the computation."""
    meta = {"data_period": _meta("terrain_r7")["data_period"] or "Copernicus DEM GLO-30",
            "as_of": _meta("masts_sites")["as_of"]}
    height = s["antenna_height_m"] if s["antenna_height_m"] is not None else None
    los = los or terrain.line_of_sight(lat, lon, s["lat"], s["lon"], mast_height=height)
    ops_here, confirmed = _ops(s["operators"]), _ops(s["operators_confirmed"])
    label = (f"{_join(for_ops)}'s nearest approximate site" if for_ops
             else "The nearest approximate site (operator unknown)")
    conf = [o for o in for_ops if o in confirmed]
    who = (f" ({_join(conf)} confirmed by Ofcom's licence register)" if conf
           else " (operator inferred from crowd-sourced data)" if for_ops else "")
    bearing = _compass(bearing_deg(lat, lon, s["lat"], s["lon"]))
    height_txt = ("antenna height from Ofcom's licence register" if los["mast_height_source"] == "ofcom_register"
                  else f"antenna height assumed {los['mast_height_m']:.0f} m")
    if los["blocked"]:
        o = los["max_obstruction_m"]
        sentence = (f"{label} is {_km(los['path_length_m'])} {bearing}{who}. Terrain rises {o:.0f} m above the "
                    f"direct line {_km(los['obstruction_distance_m'])} from here ({height_txt}), so the signal "
                    "is likely weakened, not necessarily lost.")
        if o < LOS_MARGINAL_M:
            sentence += " The margin is small and may be trees or buildings in the surface model."
        strength = "strong" if o >= LOS_STRONG_M else "moderate" if o >= LOS_MARGINAL_M else "weak"
    elif -los["max_obstruction_m"] < 1:
        sentence = (f"{label} is {_km(los['path_length_m'])} {bearing}{who}. The direct line only just clears "
                    f"the ground (by less than 1 m, {height_txt}), so trees or buildings could still be in the way.")
        strength = "weak"
    else:
        sentence = (f"{label} is {_km(los['path_length_m'])} {bearing}{who}. The direct line clears the "
                    f"ground by at least {-los['max_obstruction_m']:.0f} m ({height_txt}).")
        strength = "moderate"
    key = item_id or "los_" + ("_".join(o.lower() for o in for_ops) or "nearest")
    b.add(key, "Line of sight over Copernicus DEM GLO-30 (computed)", meta,
          f"terrain.line_of_sight from ({lat:.5f}, {lon:.5f}) to site {s['site_id']} (zetaris:Q2)",
          sorted({cell_id, h3.latlng_to_cell(s["lat"], s["lon"], H3_RES)}), sentence,
          {"site_id": s["site_id"], "operators_at_site": ops_here, "operators_confirmed": confirmed,
           "for_operators": for_ops, "los_class": los_class(los),
           # metres the ground rises above the direct line (negative: clearance), for the critic to cite
           "obstruction_margin_m": round(los["max_obstruction_m"]), **{k: v for k, v in los.items() if k != "profile"}},
          {"dem_samples": los["samples"]}, strength, ["terrain_blocking", "distance_to_mast"])
    return los


def los_items(b: Bundle, sites: list[dict], lat: float, lon: float, cell_id: str) -> dict[str, dict]:
    """Line of sight to the nearest site of each operator (and the nearest site overall).

    Returns the line-of-sight result per site id (for the operator comparison)."""
    targets: dict[str, dict] = {}
    for op in OPERATORS:
        hit = next((s for s in sites if op in _ops(s["operators"])), None)
        if hit:
            targets.setdefault(hit["site_id"], {"site": hit, "for": []})["for"].append(op)
        else:
            no_site_item(b, op, sites, cell_id)
    if sites and sites[0]["site_id"] not in targets:
        targets[sites[0]["site_id"]] = {"site": sites[0], "for": []}
    return {t["site"]["site_id"]: los_item(b, t["site"], t["for"], lat, lon, cell_id) for t in targets.values()}


# --- operator comparison and the rule-based summary ------------------------------------------

SOURCE_KIND = {"opencellid": "crowd-sourced (OpenCelliD)", "osm": "OpenStreetMap", "ofcom_wtr": "Ofcom register"}


def operator_comparison(sites: list[dict], los_by_site: dict[str, dict], lat: float, lon: float) -> list[dict]:
    """Per operator: nearest identified site within 10 km, confirmed or inferred, line of sight."""
    rows = []
    for op in OPERATORS:
        s = next((x for x in sites if op in _ops(x["operators"])), None)
        if s is None:
            rows.append({"operator": op, "site": None,
                         "note": f"No {op} site identified in our data within {SITE_RADIUS_KM:.0f} km "
                                 "(not the same as no coverage)."})
            continue
        los = los_by_site[s["site_id"]]
        bearing = bearing_deg(lat, lon, s["lat"], s["lon"])
        rows.append({
            "operator": op,
            "site": {"site_id": s["site_id"], "lat": s["lat"], "lon": s["lon"], "distance_km": round(s["distance_km"], 2),
                     "bearing_deg": round(bearing), "direction": _compass(bearing),
                     "sources": [SOURCE_KIND.get(x, x) for x in str(s["sources"]).split(",")]},
            "status": "confirmed" if op in _ops(s["operators_confirmed"]) else "inferred",
            "los": {"class": los_class(los), "blocked": los["blocked"], "obstruction_m": los["max_obstruction_m"],
                    "obstruction_distance_m": los["obstruction_distance_m"], "obstruction_lat": los["obstruction_lat"],
                    "obstruction_lon": los["obstruction_lon"], "path_length_m": los["path_length_m"],
                    "mast_height_m": los["mast_height_m"],
                    "mast_height": "measured" if los["mast_height_source"] == "ofcom_register" else "assumed"},
        })
    return rows


def map_sites(sites: list[dict]) -> list[dict]:
    """Identified sites within 10 km for the map, marked confirmed / inferred / unknown."""
    out = []
    for x in sites[:MAP_SITES_MAX]:
        ops, conf = _ops(x["operators"]), _ops(x["operators_confirmed"])
        out.append({"site_id": x["site_id"], "lat": round(x["lat"], 5), "lon": round(x["lon"], 5),
                    "distance_km": round(x["distance_km"], 2), "operators": ops, "operators_confirmed": conf,
                    "status": "confirmed" if conf else "inferred" if ops else "unknown",
                    "sources": [SOURCE_KIND.get(s, s) for s in str(x["sources"]).split(",")]})
    return out


def likely_best(rows: list[dict]) -> dict:
    """Rule-based summary: the operators with the least obstructed line of sight from the point."""
    rank = {"clear": 0, "marginal": 1, "blocked": 2}
    # Clear lines: nearest first. Obstructed lines: least obstruction first, then nearest.
    with_site = sorted((r for r in rows if r["site"]), key=lambda r: (
        rank[r["los"]["class"]], 0 if r["los"]["class"] == "clear" else r["los"]["obstruction_m"],
        round(r["site"]["distance_km"], 1), r["status"] != "confirmed"))  # ties: confirmed operators first
    missing = [r["operator"] for r in rows if not r["site"]]
    gap = (f" No site identified in our data for {_join(missing)}." if missing else "")
    base = {"reviewed_by_agents": False, "label": "Based on data, not yet reviewed by agents",
            "rule": "Operators ranked by line of sight from this point to their nearest identified site "
                    "(clear, then marginal, then blocked); clear lines by distance, obstructed ones by how "
                    "far the ground rises above the line."}
    if not with_site:
        return {**base, "operators": [], "text": f"No operator site identified within {SITE_RADIUS_KM:.0f} km in our data. "
                "This is weak evidence: crowd-sourced data is incomplete."}
    top = with_site[0]
    best_class = top["los"]["class"]
    tied = [r for r in with_site if r["los"]["class"] == best_class]
    s, how = top["site"], "confirmed by Ofcom's licence register" if top["status"] == "confirmed" else "inferred from crowd-sourced data"
    where = f"a site {s['distance_km']:.1f} km {s['direction']} ({how})"
    others = [f"{r['operator']} ({r['site']['distance_km']:.1f} km)" for r in tied[1:]]
    if best_class == "clear":
        text = f"Likely best network here: {top['operator']}, with a clear line of sight to {where}."
        if others:
            text += f" Also a clear line: {_join(others)}."
    elif best_class == "marginal":
        text = (f"No operator has a fully clear line. Least obstructed: {top['operator']}, {where}, blocked by only "
                f"{top['los']['obstruction_m']:.0f} m (possibly trees or buildings).")
    else:
        text = (f"No operator has a clear line of sight to an identified site from here. Least obstructed: "
                f"{top['operator']}, {where}, with terrain {top['los']['obstruction_m']:.0f} m above the line.")
    # Every operator with a clear line is a good option; otherwise only the least obstructed one.
    chosen = tied if best_class == "clear" else [top]
    return {**base, "operators": [r["operator"] for r in chosen], "line_of_sight": best_class, "text": text + gap}


# --- live sources ----------------------------------------------------------------------------

def sample_tag(r: live.LiveResult, lat: float, lon: float) -> str:
    """ "(saved sample, offline mode)", naming the sample place when it is not this point."""
    if r.sample_place in live.SAMPLE_SLUGS:
        name, s_lat, s_lon = SAMPLE_LOCATIONS[live.SAMPLE_SLUGS.index(r.sample_place)]
        km = live.km(lat, lon, s_lat, s_lon)
        if km > 2:
            return f" (saved sample for {name.split(' (')[0]}, {km:.0f} km away, offline mode)"
    return " (saved sample, offline mode)"


def live_items(b: Bundle, w: live.LiveResult, m: live.LiveResult, f: live.LiveResult, cell_id: str) -> None:
    tag = {"live": "", "cached": " (cached copy: the live call failed)", "sample": " (saved sample, offline mode)"}
    here = h3.cell_to_latlng(cell_id)

    def _tag(r: live.LiveResult) -> str:
        return sample_tag(r, *here) if r.status == "sample" else tag.get(r.status, "")

    if w.data:
        d, n, x = w.data, w.data["now"], w.data["next_24h"]
        verdict = {"none": "no severe weather expected", "moderate": "moderate", "severe": "severe"}[d["severity"]]
        why = f" ({'; '.join(d['reasons'])})" if d["reasons"] else ""
        b.add("weather", "Open-Meteo forecast", w.to_dict(), w.url, [cell_id],
              f"Weather now: {n['weather']}, wind {n['wind_kmh']:.0f} km/h gusting {n['gust_kmh']:.0f} km/h, rain "
              f"{n['precipitation_mm_h']:.1f} mm/h. Next 24 h: gusts up to {x['max_gust_kmh']:.1f} km/h, rain up to "
              f"{x['max_precipitation_mm_h']:.1f} mm/h: {verdict}{why}.{_tag(w)}",
              {"severity": d["severity"], "now": n, "next_24h": x}, {"forecast_hours": x["hours"]},
              _live_strength(w, "moderate"), ["weather"])
    else:
        b.errors.append(f"weather: {w.error}")

    if m.data:
        ws, region = m.data["warnings"], m.data["region"]
        if ws:
            parts = [f"{x['level']} {' and '.join(x['types'])} warning ({'in force' if x['in_force'] else 'from ' + _uk_time(x['valid_from'])}"
                     f" until {_uk_time(x['valid_to'])}{', covering this district' if x['district_listed'] else ', elsewhere in the region'})"
                     for x in ws]
            finding = f"Met Office warnings for {region}: " + "; ".join(parts) + "."
            strength = "strong" if any(x["district_listed"] and x["in_force"] for x in ws) else "moderate"
        else:
            finding, strength = f"No Met Office weather warnings are in force or due for {region}.", "moderate"
        b.add("met_office_warnings", "Met Office severe weather warnings (RSS)", m.to_dict(), m.url, [cell_id],
              finding + _tag(m), {"region": region, "warnings": ws}, {"warnings": len(ws)},
              _live_strength(m, strength), ["weather"])
    else:
        b.errors.append(f"met_office_warnings: {m.error}")

    if f.status == "not_covered":
        b.add("flood_warnings", "Environment Agency flood monitoring", f.to_dict(), f.url, [cell_id],
              f"Flood warnings are not covered here: the Environment Agency feed is England only and this point is "
              f"in {f.data['country']}, so floods are unknown, not absent.",
              None, None, "weak", ["active_incident"])
    elif f.data:
        fs = f.data["warnings"]
        if fs:
            finding = (f"{len(fs)} flood alert{'s' if len(fs) > 1 else ''} or warning{'s' if len(fs) > 1 else ''} within "
                       f"{f.data['radius_km']} km: " + "; ".join(f"{x['severity']} for {x['area']} (raised "
                                                              f"{_uk_time(x['time_raised'])})" for x in fs) + ".")
        else:
            finding = f"No flood alerts or warnings within {f.data['radius_km']} km."
        b.add("flood_warnings", "Environment Agency flood monitoring", f.to_dict(), f.url, [cell_id],
              finding + _tag(f), {"warnings": fs, "via": f.data["via"]}, {"warnings": len(fs)},
              _live_strength(f, "moderate"), ["active_incident"])
    else:
        b.errors.append(f"flood_warnings: {f.error}")


# --- the bundle ------------------------------------------------------------------------------

def resolve(query_text: str | None, lat: float | None, lon: float | None) -> tuple[dict, live.LiveResult]:
    """The point to investigate, with its place details from postcodes.io."""
    if query_text:
        res = geocode.lookup(query_text)
        if not res.data.get("found"):
            raise LookupError(f"No postcode or place found for {query_text!r}")
        hit = res.data["results"][0]
        if hit["kind"] == "place":  # places carry no country/region detail for the warnings: add the nearest postcode
            near = geocode.reverse(hit["lat"], hit["lon"]).data.get("nearest") or {}
            hit = {**near, **{k: v for k, v in hit.items() if v is not None}, "nearest_postcode": near.get("postcode")}
        return hit, res
    res = geocode.reverse(lat, lon)
    near = res.data.get("nearest") or {}
    return {**near, "kind": "point", "label": f"{lat:.5f}, {lon:.5f}", "lat": lat, "lon": lon,
            "nearest_postcode": near.get("postcode"), "cell_id": h3.latlng_to_cell(lat, lon, H3_RES)}, res


def build_bundle(query_text: str | None = None, lat: float | None = None, lon: float | None = None) -> dict:
    b = Bundle()
    point, geo = resolve(query_text, lat, lon)
    lat, lon = point["lat"], point["lon"]
    cell_id = h3.latlng_to_cell(lat, lon, H3_RES)
    ring = sorted(h3.grid_ring(cell_id, 1))

    approx = point.get("approximate")
    where = ", ".join(x for x in [point.get("admin_district"), point.get("region"), point.get("country")] if x)
    if point["kind"] == "point":
        dist = geo.data.get("distance_m")
        near = (f", nearest postcode {point['nearest_postcode']}" + (f" ({dist:.0f} m away)" if dist is not None else "")
                if point.get("nearest_postcode") else "")
        location = f"The point {lat:.5f}, {lon:.5f} is in {where or 'the UK'}{near}, in H3 cell {cell_id}."
    else:
        location = (f"{point['label']} ({where}) is at {lat:.5f}, {lon:.5f}, in H3 cell {cell_id}."
                    + (" This is an approximate match for the search text." if approx else ""))
    b.add("location", "postcodes.io", geo.to_dict(), geo.url, [cell_id], location,
          {k: point.get(k) for k in ("kind", "label", "postcode", "nearest_postcode", "admin_district", "region",
                                     "country", "lat", "lon")},
          None, "weak" if approx else "strong", [])

    # Live sources run while Zetaris answers.
    with ThreadPoolExecutor(max_workers=3) as pool:
        fw = pool.submit(weather.forecast, lat, lon)
        fm = pool.submit(met_office_warnings.for_place, point, lat, lon)
        ff = pool.submit(flood_warnings.near, lat, lon, point.get("country"))

        cell, sites, ctx, tiles = None, [], None, None
        quarter = _meta("ookla_mobile_r7")["data_period"]
        try:
            sql, _ = cell_sql(cell_id)
            rows = b.zetaris("Q1", sql)
            cell = rows[0] if rows else None
            if cell is None:
                b.errors.append(f"cell {cell_id} is not in the UK grid")
            sites = b.zetaris("Q2", sites_sql(lat, lon))
            for s in sites:
                s["has_microwave_backhaul"] = _bool(s["has_microwave_backhaul"])
            ctx_rows = b.zetaris("Q3", UK_CONTEXT_SQL, cached=True)
            ctx = ctx_rows[0] if ctx_rows else None
            tiles = b.zetaris("Q4", tiles_sql(lat, lon, quarter, TILE_MAP_RADIUS_KM))
        except ZetarisError as e:
            b.errors.append(f"Zetaris: {e}")

        if tiles is not None:
            nearby_speed_item(b, tiles, quarter, cell_id)
        if cell:
            cell["history"] = sorted(json.loads(cell.pop("history_json") or "[]"), key=lambda h: h["quarter"])
            speed_items(b, cell, cell_id, ring, ctx)
        site_items(b, sites, lat, lon, cell_id)
        los_by_site = los_items(b, sites, lat, lon, cell_id) if sites else {}
        operators = operator_comparison(sites, los_by_site, lat, lon)
        if cell:
            terrain_item(b, cell, cell_id)
            ofcom_item(b, cell, cell_id)
        live_items(b, fw.result(), fm.result(), ff.result(), cell_id)

    return {
        "query": query_text, "point": point, "cell_id": cell_id, "neighbour_cell_ids": ring,
        "generated_at": live.now_iso(), "sample_mode": sample_mode(),
        "items": b.items, "operators": operators, "likely_best": likely_best(operators),
        "sites": map_sites(sites),
        "zetaris_queries": b.queries, "errors": b.errors, "limitations": LIMITATIONS,
        "resident_check": "Check your address on Ofcom's mobile coverage checker: https://www.ofcom.org.uk/mobile-coverage-checker",
    }
