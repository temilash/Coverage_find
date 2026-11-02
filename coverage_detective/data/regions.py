"""Named regions as WGS84 polygons: the UK, a nation, a local authority, a county made of its districts,
or a bounding box. Boundaries: data/raw/boundary/uk_countries.geojson (ONS countries) and lad_2024.geojson
(ONS local authority districts, December 2024)."""

from __future__ import annotations

import json

from shapely.geometry import box, shape
from shapely.ops import unary_union

from coverage_detective.config import RAW

# Two-tier counties, by their district councils (ONS December 2024 names).
COUNTIES = {
    "Surrey": ["Elmbridge", "Epsom and Ewell", "Guildford", "Mole Valley", "Reigate and Banstead", "Runnymede",
               "Spelthorne", "Surrey Heath", "Tandridge", "Waverley", "Woking"],
}


def _features(path: str, prefix: str) -> dict[str, object]:
    gj = json.loads((RAW / "boundary" / path).read_text())
    out = {}
    for f in gj["features"]:
        name = next(v for k, v in f["properties"].items() if k.upper().startswith(prefix) and k.upper().endswith("NM"))
        out[name] = shape(f["geometry"])
    return out


def nations() -> dict[str, object]:
    """Nation polygons from the ONS countries file."""
    return _features("uk_countries.geojson", "CTRY")


def local_authorities() -> dict[str, object]:
    return _features("lad_2024.geojson", "LAD")


def region(name: str | None = None, bbox: str | None = None):
    """(polygon, label) for "UK", a nation, a local authority, a county in COUNTIES, or a bbox "w,s,e,n"."""
    if bbox:
        w, s, e, n = (float(v) for v in bbox.split(","))
        return box(w, s, e, n), f"bbox {bbox}"
    ns = nations()
    if name in ns:
        return ns[name], name
    if name and name.upper() in ("UK", "UNITED KINGDOM"):
        return unary_union(list(ns.values())), "United Kingdom"
    lads = {k.lower(): (k, v) for k, v in local_authorities().items()}
    county = next((c for c in COUNTIES if c.lower() == (name or "").lower()), None)
    if county:
        return unary_union([lads[d.lower()][1] for d in COUNTIES[county]]), county
    if (name or "").lower() in lads:
        label, geom = lads[name.lower()]
        return geom, label
    raise LookupError(f"no nation, county or local authority called {name!r} (or pass a bbox)")
