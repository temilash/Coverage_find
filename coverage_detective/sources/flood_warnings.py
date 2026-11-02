"""Environment Agency flood warnings in force near a point (England only; no key; OGL v3).

The EA API (/id/floods?lat=&long=&dist=10), through the cached client. EA measures the
distance to each flood area's polygon, so long river and coast areas are found even when their
centre is far away.

Severity levels: 1 severe flood warning, 2 flood warning, 3 flood alert, 4 warning no
longer in force (dropped). Outside England the answer is "not_covered", never an empty
list, so it cannot be mistaken for "no floods": SEPA (Scotland), NRW (Wales) and DfI
(Northern Ireland) run their own services, not wired in yet.
"""

from __future__ import annotations

from coverage_detective.sources import live

SOURCE = "ea_flood_monitoring"
API = "https://environment.data.gov.uk/flood-monitoring/id/floods"
TTL_S = 15 * 60
RADIUS_KM = 10
NOT_COVERED = "not covered (England only for now)"
SEVERITY = {1: "severe flood warning", 2: "flood warning", 3: "flood alert"}


def _item(f: dict, distance_km=None) -> dict:
    level = f["severity_level"]
    return {"severity_level": level, "severity": SEVERITY.get(level, f.get("severity")),
            "area": f.get("area"), "ea_area": f.get("ea_area"), "river_or_sea": f.get("river_or_sea"),
            "time_raised": f.get("time_raised"), "time_severity_changed": f.get("time_severity_changed"),
            "flood_area_id": f.get("flood_area_id"), "distance_km": distance_km}


def _summary(items: list[dict]) -> dict:
    items = sorted(items, key=lambda i: (i["severity_level"], i["distance_km"] or 0))
    return {"covered": True, "radius_km": RADIUS_KM, "count": len(items), "warnings": items,
            "highest": items[0]["severity"] if items else None}


def via_api(lat: float, lon: float) -> live.LiveResult:
    params = {"lat": round(lat, 4), "long": round(lon, 4), "dist": RADIUS_KM}
    res = live.get(SOURCE, API, params, ttl_s=TTL_S, sample=live.nearest_sample(lat, lon))
    if res.body is not None:
        items = [_item({"severity_level": f.get("severityLevel"), "severity": f.get("severity"),
                        "area": f.get("description"), "ea_area": f.get("eaAreaName"),
                        "river_or_sea": (f.get("floodArea") or {}).get("riverOrSea"),
                        "time_raised": f.get("timeRaised"), "time_severity_changed": f.get("timeSeverityChanged"),
                        "flood_area_id": f.get("floodAreaID")})
                 for f in res.body.get("items", []) if f.get("severityLevel") in SEVERITY]
        res.data = _summary(items)
    return res


def near(lat: float, lon: float, country: str | None) -> live.LiveResult:
    """Flood alerts and warnings in force within 10 km. `country` from postcodes.io."""
    if country and country != "England":
        res = live.LiveResult(SOURCE, API, "not_covered", live.now_iso())
        res.data = {"covered": False, "message": NOT_COVERED, "country": country}
        return res
    return via_api(lat, lon)
