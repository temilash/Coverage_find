"""postcodes.io: postcode / place name -> location, and location -> nearest postcode.

postcodes.io is free, needs no key, and is built on ONS / OS open data (OGL v3).

    lookup("WC2N 5DU")      postcode endpoint
    lookup("Rhayader")      places endpoint (OS Open Names)
    reverse(54.445, -3.105) nearest postcode (2 km, then postcodes.io's wide search, 20 km)
"""

from __future__ import annotations

import re

import h3

from coverage_detective.config import H3_RES
from coverage_detective.sources import live

SOURCE = "postcodes_io"
BASE = "https://api.postcodes.io"
TTL_S = 30 * 24 * 3600
SAMPLE_REVERSE_M = 2000  # the live lookup's radius: a saved postcode further away is not reported

# Full UK postcode, with or without the space (e.g. "WC2N 5DU", "ld66aa").
POSTCODE_RE = re.compile(r"^[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}$", re.I)


def is_postcode(query: str) -> bool:
    return bool(POSTCODE_RE.match(query.strip()))


def normalise_postcode(query: str) -> str:
    pc = re.sub(r"\s+", "", query).upper()
    return f"{pc[:-3]} {pc[-3:]}"


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _cell(lat: float, lon: float) -> str:
    return h3.latlng_to_cell(lat, lon, H3_RES)


def _from_postcode(p: dict) -> dict:
    return {"kind": "postcode", "label": p["postcode"], "postcode": p["postcode"],
            "lat": p["latitude"], "lon": p["longitude"],
            "admin_district": p.get("admin_district"), "admin_county": p.get("admin_county"),
            "admin_ward": p.get("admin_ward"), "parish": p.get("parish"), "pfa": p.get("pfa"),
            "region": p.get("region"), "country": p.get("country"),
            "cell_id": _cell(p["latitude"], p["longitude"])}


def _from_place(p: dict) -> dict:
    district = p.get("district_borough") or p.get("county_unitary") or ""
    district = district.split(" - ")[0] or None  # "Powys - Powys" -> "Powys"
    return {"kind": "place", "label": p["name_1"], "local_type": p.get("local_type"),
            "lat": p["latitude"], "lon": p["longitude"], "admin_district": district,
            "region": p.get("region"), "country": p.get("country"),
            "cell_id": _cell(p["latitude"], p["longitude"])}


def _places(q: str, sample: str) -> live.LiveResult:
    return live.get(SOURCE, f"{BASE}/places", {"q": q, "limit": 6}, ttl_s=TTL_S, sample=sample)


def lookup(query: str) -> live.LiveResult:
    """Postcode or place name -> result.data = {"query", "results": [...], "found"}."""
    q = query.strip()
    sample = f"lookup_{slug(q)}"
    if is_postcode(q):
        pc = normalise_postcode(q)
        res = live.get(SOURCE, f"{BASE}/postcodes/{pc.replace(' ', '')}", ttl_s=TTL_S, sample=sample,
                       ok_statuses=(200, 404))
        body = res.body or {}
        results = [_from_postcode(body["result"])] if body.get("result") else []
    else:
        res = _places(q, sample)
        results = [_from_place(p) for p in ((res.body or {}).get("result") or [])]
        words = [w for w in re.split(r"\s+", q) if len(w) > 3]
        if not results and len(words) > 1 and res.status != "unavailable":
            # postcodes.io's place list misses some names (e.g. "Great Langdale"); retry with
            # the most distinctive word and mark the matches as approximate.
            retry = _places(max(words, key=len), sample + "_retry")
            results = [{**_from_place(p), "approximate": True} for p in ((retry.body or {}).get("result") or [])]
    res.data = {"query": q, "found": bool(results), "results": results}
    return res


def reverse(lat: float, lon: float) -> live.LiveResult:
    """Nearest postcode to a point -> result.data = {"found", "nearest": {...}, "distance_m"}."""
    sample = f"reverse_{live.nearest_sample(lat, lon)}"
    res = live.get(SOURCE, f"{BASE}/postcodes", {"lat": lat, "lon": lon, "radius": 2000, "limit": 1},
                   ttl_s=TTL_S, sample=sample)
    if res.status != "unavailable" and not (res.body or {}).get("result"):
        res = live.get(SOURCE, f"{BASE}/postcodes", {"lat": lat, "lon": lon, "widesearch": "true", "limit": 1},
                       ttl_s=TTL_S, sample=sample + "_wide")
    found = (res.body or {}).get("result") or []
    distance = found[0].get("distance") if found else None
    if found and res.status == "sample":  # a saved response is for a sample place: measure from this point instead
        distance = live.km(lat, lon, found[0]["latitude"], found[0]["longitude"]) * 1000
        if distance > SAMPLE_REVERSE_M:  # not this point's postcode: say nothing rather than another place's
            found = []
    if found:
        nearest = _from_postcode(found[0])
        res.data = {"found": True, "nearest": nearest, "distance_m": distance}
    else:
        res.data = {"found": False, "nearest": None, "distance_m": None}
    return res


def place_name(nearest: dict | None) -> str | None:
    """'Lakes, Westmorland and Furness': parish (or ward when unparished) and district."""
    if not nearest:
        return None
    local = nearest.get("parish") or nearest.get("admin_ward")
    if local and "unparished" in local.lower():
        local = nearest.get("admin_ward")
    parts = [p for p in (local, nearest.get("admin_district")) if p]
    return ", ".join(dict.fromkeys(parts)) or None
