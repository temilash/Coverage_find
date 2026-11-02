"""Candidate fixes for one gap zone or improvement opportunity (rule-based).

suggest_opportunity_fix() does the same for improvement opportunities: add_operators, capacity_upgrade,
a (small) new_site when no site is within 5 km, or gather_data; limited data adds "measure first".

suggest_fix()   fix type by rule, from the zone's raw factors:
  new_site           most people are in cells with no site within 5 km, or with terrain
                     blocking every nearby site
  add_operators      few operators (mean < 2) although a site is nearby
  capacity_upgrade   measured speeds are low although sites are nearby and the lines are clear
  gather_data        thin data (< 10 tests): measure before deciding (also added as a second
                     step when another fix is suggested on thin data)

candidates()    possible mast positions, scored by people reached:
  - new sites: up to 30 local high points from the DEM (local maxima ranked by how much they
    stand above their surroundings), inside the zone or up to 5 km around it, >= 500 m apart;
  - existing sites within 10 km of the zone (for "add operators" / upgrades);
  - for each: line of sight (mast 25 m, or the register height for existing sites; receiver
    1.5 m) to every populated cell centre in the zone and within 8 km. People reached = the
    population of cells with a clear line; people gaining = those cells that are poorly served
    (in this zone, or in other zones nearby). Ranked by this zone's people gaining, then other
    poorly served people, then everyone reached.
Limits: simplified line of sight to cell centres (no radio propagation model, no Fresnel
zone or clutter), approximate sites, no planning, land, power or backhaul checks.
"""

from __future__ import annotations

import math

import numpy as np
from scipy import ndimage
from scipy.spatial import cKDTree

from coverage_detective import config
from coverage_detective.data import terrain

M_PER_DEG = 111_320.0
GRID_M = 150.0            # DEM sampling for high points
BUFFER_KM = 5.0           # candidates up to this far outside the zone
REACH_KM = 8.0            # cells within this distance of a candidate are checked
MIN_SPACING_M = 500.0
MAX_NEW = 30
MAX_EXISTING = 15
EXISTING_KM = 10.0
NEW_MAST_M = 25.0

FIX_LABELS = {"new_site": "New site", "add_operators": "Add operators to an existing site",
              "capacity_upgrade": "Capacity upgrade", "gather_data": "Gather measurements first"}


def _xy(lat, lon, lat0):
    lat, lon = np.asarray(lat, float), np.asarray(lon, float)
    return np.column_stack([lon * M_PER_DEG * math.cos(math.radians(lat0)), lat * M_PER_DEG])


def _tests(n: int) -> str:
    return f"{n} Ookla test" + ("" if n == 1 else "s")


def suggest_fix(zone: dict, sites_within_5km: int) -> dict:
    """Fix type and why, from the zone's people by criterion, operators and tests."""
    pop = zone["population"] or 1.0
    unserved = (zone["pop_no_site"] + zone["pop_blocked"]) / pop
    slow = zone["pop_slow"] / pop
    thin = zone["tests"] < config.GAP_MIN_TESTS
    why = []
    if unserved >= 0.5:
        kind = "new_site"
        parts = []
        if zone["pop_no_site"] >= 1:
            parts.append(f"no identified site within 5 km ({zone['pop_no_site']:.0f} people)")
        if zone["pop_blocked"] >= 1:
            parts.append(f"terrain blocking the line to every site within 5 km ({zone['pop_blocked']:.0f} people)")
        why.append(f"{unserved:.0%} of the zone's people live in cells with " + " or ".join(parts) + ".")
    elif zone["operators_mean"] < 2 and sites_within_5km > 0:
        kind = "add_operators"
        why.append(f"On average only {zone['operators_mean']:.1f} operators have sites within 5 km, but "
                   f"{sites_within_5km} approximate sites are nearby that more operators could share.")
    elif slow >= 0.5:
        kind = "capacity_upgrade"
        why.append(f"{slow:.0%} of the zone's people are in cells measured below {config.GAP_SLOW_MBPS:.0f} Mbps "
                   "although sites are nearby with a clear line.")
    else:
        kind = "gather_data"
        why.append("No single cause dominates the zone's cells.")
    secondary = None
    if thin and kind != "gather_data":
        secondary = "gather_data"
        why.append(f"Only {_tests(zone['tests'])} in the zone, so measure first to confirm.")
    elif thin:
        why.append(f"Only {_tests(zone['tests'])} in the zone.")
    return {"type": kind, "label": FIX_LABELS[kind], "why": why,
            "secondary": secondary, "secondary_label": FIX_LABELS.get(secondary),
            "rule_based": True, "note": "Rule-based suggestion from the zone data and the terrain model."}


def suggest_opportunity_fix(zone: dict, sites_within_5km: int) -> dict:
    """Upgrade for an improvement opportunity (not a confirmed gap), from its people per reason."""
    pop = zone["population"] or 1.0
    share = {r: zone[f"pop_{r}"] / pop for r in ("slow", "falling", "few_operators", "capacity")}
    thin = zone["tests"] < config.GAP_MIN_TESTS
    why = []
    if share["few_operators"] >= 0.5 and sites_within_5km > 0:
        kind = "add_operators"
        why.append(f"{share['few_operators']:.0%} of the zone's people are in cells where only 1-2 operators are "
                   f"identified, and {sites_within_5km} approximate sites nearby could host more.")
    elif max(share["capacity"], share["slow"], share["falling"]) >= 0.5 and sites_within_5km > 0:
        kind = "capacity_upgrade"
        main = max(("capacity", "slow", "falling"), key=lambda r: share[r])
        words = {"capacity": "show a busy-network (capacity) signal", "slow": "measure well below the area or UK median",
                 "falling": "have falling speeds"}
        why.append(f"{share[main]:.0%} of the zone's people are in cells that {words[main]}, with sites nearby.")
    elif sites_within_5km == 0:
        kind = "new_site"
        why.append("No approximate site lies within 5 km of the zone's cells, so a small new site could help.")
    else:
        kind = "gather_data"
        why.append("No single weakness dominates the zone's cells.")
    secondary = None
    if thin and kind != "gather_data":
        secondary = "gather_data"
        why.append(f"Only {_tests(zone['tests'])} in the zone (limited data), so measure first to confirm.")
    elif thin:
        why.append(f"Only {_tests(zone['tests'])} in the zone (limited data).")
    return {"type": kind, "label": FIX_LABELS[kind], "why": why,
            "secondary": secondary, "secondary_label": FIX_LABELS.get(secondary),
            "rule_based": True, "note": "Rule-based suggestion for an improvement opportunity, from the zone data and the terrain model."}


def high_points(zone_cells: list[dict], dem=None) -> list[dict]:
    """Local high points in and up to 5 km around the zone, >= 500 m apart, most prominent first."""
    dem = dem or terrain.dem()
    lats = np.array([c["lat"] for c in zone_cells])
    lons = np.array([c["lon"] for c in zone_cells])
    lat0 = float(lats.mean())
    pad_lat = (BUFFER_KM + 1.5) / 111.0
    pad_lon = (BUFFER_KM + 1.5) / (111.32 * math.cos(math.radians(lat0)))
    dlat = GRID_M / M_PER_DEG
    dlon = GRID_M / (M_PER_DEG * math.cos(math.radians(lat0)))
    glat = np.arange(lats.min() - pad_lat, lats.max() + pad_lat, dlat)
    glon = np.arange(lons.min() - pad_lon, lons.max() + pad_lon, dlon)
    LAT, LON = np.meshgrid(glat, glon, indexing="ij")
    z = dem.elevation(LAT.ravel(), LON.ravel()).reshape(LAT.shape).astype(float)
    peaks = (ndimage.maximum_filter(z, size=7) == z) & (z > 0)
    prominence = z - ndimage.uniform_filter(z, size=11)
    # Inside the zone or within 5 km of it (cell centres; a cell spans ~1.2 km).
    zone_tree = cKDTree(_xy(lats, lons, lat0))
    d, _ = zone_tree.query(_xy(LAT[peaks], LON[peaks], lat0))
    keep = d <= (BUFFER_KM + 1.2) * 1000
    cand = sorted(zip(prominence[peaks][keep], z[peaks][keep], LAT[peaks][keep], LON[peaks][keep], d[keep]), reverse=True)
    chosen: list[dict] = []
    for prom, elev, la, lo, dist in cand:
        p = _xy([la], [lo], lat0)[0]
        if all(np.hypot(*(p - _xy([c["lat"]], [c["lon"]], lat0)[0])) >= MIN_SPACING_M for c in chosen):
            chosen.append({"kind": "new_site", "lat": float(la), "lon": float(lo), "ground_m": round(float(elev), 1),
                           "prominence_m": round(float(prom), 1), "in_zone": bool(dist <= 1200),
                           "mast_height_m": NEW_MAST_M, "mast_height": "proposed"})
        if len(chosen) >= MAX_NEW:
            break
    return chosen


def existing_candidates(zone_cells: list[dict], sites: list[dict]) -> list[dict]:
    """Existing approximate sites within 10 km of the zone, nearest first."""
    if not sites:
        return []
    lat0 = float(np.mean([c["lat"] for c in zone_cells]))
    tree = cKDTree(_xy([c["lat"] for c in zone_cells], [c["lon"] for c in zone_cells], lat0))
    d, _ = tree.query(_xy([s["lat"] for s in sites], [s["lon"] for s in sites], lat0))
    order = [i for i in np.argsort(d) if d[i] <= EXISTING_KM * 1000][:MAX_EXISTING]
    out = []
    for i in order:
        s = sites[i]
        h = s.get("antenna_height_m")
        out.append({"kind": "existing_site", "site_id": s["site_id"], "lat": float(s["lat"]), "lon": float(s["lon"]),
                    "operators": [o for o in str(s.get("operators") or "").split(",") if o],
                    "distance_to_zone_km": round(float(d[i]) / 1000, 2),
                    "mast_height_m": float(h) if h is not None and not math.isnan(h) else NEW_MAST_M,
                    "mast_height": "register" if h is not None and not math.isnan(h) else "assumed"})
    return out


def evaluate(cands: list[dict], zone_cells: list[dict], area_cells: list[dict], dem=None) -> list[dict]:
    """People reached by each candidate (clear line of sight to the cell centre)."""
    dem = dem or terrain.dem()
    zone_ids = {c["cell_id"] for c in zone_cells}
    lat0 = float(np.mean([c["lat"] for c in zone_cells]))
    targets = {c["cell_id"]: c for c in area_cells}
    targets.update({c["cell_id"]: {**c, "poor": True} for c in zone_cells})
    tlist = list(targets.values())
    tree = cKDTree(_xy([t["lat"] for t in tlist], [t["lon"] for t in tlist], lat0))
    for cand in cands:
        near = set(tree.query_ball_point(_xy([cand["lat"]], [cand["lon"]], lat0)[0], REACH_KM * 1000))
        near |= {i for i, t in enumerate(tlist) if t["cell_id"] in zone_ids}
        reached = []
        for i in near:
            t = tlist[i]
            los = terrain.line_of_sight(t["lat"], t["lon"], cand["lat"], cand["lon"],
                                        mast_height=cand["mast_height_m"], source=dem)
            if not los["blocked"]:
                reached.append({"cell_id": t["cell_id"], "population": t["population"], "poor": bool(t.get("poor")),
                                "in_zone": t["cell_id"] in zone_ids, "lat": t["lat"], "lon": t["lon"]})
        cand["cells_checked"] = len(near)
        cand["reached"] = sorted(reached, key=lambda r: -r["population"])
        cand["people_reached"] = round(sum(r["population"] for r in reached))
        cand["poor_people_gaining"] = round(sum(r["population"] for r in reached if r["poor"]))
        cand["zone_people_gaining"] = round(sum(r["population"] for r in reached if r["in_zone"]))
    return cands


def plan(zone: dict, zone_cells: list[dict], area_cells: list[dict], sites: list[dict], dem=None, top: int = 3,
         kind: str = "gap") -> dict:
    """Fix suggestion + the top candidates (new and existing sites) for one zone (gap or opportunity)."""
    lat0 = float(np.mean([c["lat"] for c in zone_cells]))
    tree = cKDTree(_xy([c["lat"] for c in zone_cells], [c["lon"] for c in zone_cells], lat0))
    d, _ = tree.query(_xy([s["lat"] for s in sites], [s["lon"] for s in sites], lat0)) if sites else ([], None)
    within_5 = int(np.sum(np.asarray(d) <= 5000)) if sites else 0
    fix = suggest_opportunity_fix(zone, within_5) if kind == "opportunity" else suggest_fix(zone, within_5)
    cands = evaluate(high_points(zone_cells, dem) + existing_candidates(zone_cells, sites), zone_cells, area_cells, dem)
    # This zone's people first, then poorly served people in other zones nearby, then everyone reached.
    cands.sort(key=lambda c: (-c["zone_people_gaining"], -c["poor_people_gaining"], -c["people_reached"]))
    best_new = [c for c in cands if c["kind"] == "new_site"][:top]
    best_existing = [c for c in cands if c["kind"] == "existing_site"][:top]
    return {"fix": fix, "candidates": best_new, "existing_sites": best_existing,
            "evaluated": {"new_sites": sum(c["kind"] == "new_site" for c in cands),
                          "existing_sites": sum(c["kind"] == "existing_site" for c in cands)},
            "method": {"mast_height_m": NEW_MAST_M, "receiver_m": terrain.RX_HEIGHT_M, "reach_km": REACH_KM,
                       "buffer_km": BUFFER_KM, "min_spacing_m": MIN_SPACING_M},
            "limits": ["Simplified line of sight to cell centres, not a radio propagation model (no Fresnel zone, "
                       "clutter or indoor loss).",
                       "No planning, land, power or backhaul checks; positions are DEM high points, not real plots.",
                       "Existing sites are approximate positions from crowd-sourced data and Ofcom's register.",
                       "Copernicus GLO-30 is a surface model (trees and buildings included)."]}
