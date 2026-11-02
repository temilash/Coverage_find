"""Where the open LiDAR DSM / DTM tiles are, per nation, and how big they are, without downloading them.

    from coverage_detective.lidar import catalog
    tiles = catalog.england_tiles(region, res=2)        # each: dataset, tile, bounds (EPSG:27700), size, how

Sources (checked Nov 2026; details in docs/data_sources.md):
  England   Environment Agency LIDAR Composite 2022 (DTM; first-return DSM), 1 m and 2 m, 5 km tiles on the OS
            grid. Tiles: https://environment.data.gov.uk/tiles/collections/survey/<product>/2022/<res>/<tile>
            (a zip per tile; the server sends no Content-Length and refuses HEAD, so sizes are measured samples).
            The same data is served by WCS, which returns any window as GeoTIFF.
  Wales     Welsh Government LiDAR 2020-22, 1 m DSM / DTM, 1 km tiles listed by the DataMapWales WFS layer
            geonode:welsh_government_lidar_tile_catalogue_2020_2023 (float32 GeoTIFFs on Azure, HEAD works),
            plus Wales-wide float32 COGs (overviews give 2 m).
  Scotland  Scottish Remote Sensing Portal bucket s3://srsp-open-data (public; the listing carries each size).
            Only collections under the Open Government Licence are used (SCOTLAND_OGL).
  NI        OSNI Open Data River Basin LiDAR 2004-2014 and Belfast City 2006 on OpenDataNI: one zip per river
            basin with 1 m (some 0.5 m) ESRI ASCII DSM and DTM on the Irish Grid; sizes from the CKAN API.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

import httpx
from pyproj import Transformer
from shapely.geometry import Polygon, box, shape
from shapely.ops import transform, unary_union

from coverage_detective.config import RAW, ROOT
from coverage_detective.sources.ofcom_wtr import GRID_LETTERS

CACHE = ROOT / "data" / "cache" / "lidar"
TIMEOUT = 60

# England: measured zip sizes per 5 km tile and layer (2 Nov 2026; SU9545 Guildford, SX6060 Dartmoor, NY3005
# Lake District). 2 m: 21.3-22.7 MB per layer; 1 m: DTM 70.6 MB, first-return DSM 76.0 MB (SU9545).
EA_BASE = "https://environment.data.gov.uk/tiles/collections/survey"
EA_PRODUCTS = {"dtm": "lidar_composite_dtm", "dsm": "lidar_composite_first_return_dsm"}
EA_TILE_BYTES = {2: {"dtm": 21_981_458, "dsm": 22_342_999}, 1: {"dtm": 70_576_022, "dsm": 75_993_507}}

WALES_WFS = "https://datamap.gov.wales/geoserver/ows"
WALES_LAYER = "geonode:welsh_government_lidar_tile_catalogue_2020_2023"

SCOTLAND_BUCKET = "https://srsp-open-data.s3.eu-west-2.amazonaws.com"
# Collections whose metadata (api.remotesensing.data.gov.scot) states the Open Government Licence v3. Left out:
# HES 2010, 2010s10, 2016-2017 and Luing (Non-Commercial Government Licence).
SCOTLAND_OGL = ["phase-1", "phase-2", "phase-3", "phase-4", "phase-5", "phase-6", "national-lidar-programme",
                "orkney-islands-council-23", "outer-hebrides/2019", "coastal/coastal-2025-2026", "hes/hes-2016",
                "hes/hes-2017", "hes/hes-2017sp3"]

NI_CKAN = "https://admin.opendatani.gov.uk/api/3/action/package_search"
NI_EXTENTS = "https://opendatani.blob.core.windows.net/dfiriverslidar/RiverBasin_LIDAR_Extents.zip"

_TO_BNG = Transformer.from_crs("EPSG:4326", "EPSG:27700", always_xy=True)


@dataclass
class Tile:
    nation: str
    dataset: str          # e.g. "lidar_composite_dtm 2022 2m", "phase-3/dsm"
    layer: str            # "dsm" or "dtm" (or "dsm+dtm" for a zip holding both)
    tile: str             # tile name
    bounds: tuple | None  # (minx, miny, maxx, maxy) in EPSG:27700, or None (NI: whole zip)
    size: int | None      # bytes
    size_how: str         # "listed" (catalogue), "head" (Content-Length), "sample" (measured sample), "estimated"
    url: str


# ------------------------------------------------------------------ OS grid tile names


def grid_tile_bounds(name: str) -> tuple[float, float, float, float] | None:
    """Bounds (EPSG:27700) of an OS grid tile name: "HY20" (10 km), "NG2003" (1 km), "NS16NE" (5 km quadrant of a
    10 km square), "SU9545" read as a 1 km reference (the caller sets the tile size when it differs). None for a
    name that is not a grid reference."""
    m = re.match(r"([A-HJ-Z]{2})(\d{2}|\d{4}|\d{6})(NE|NW|SE|SW)?(?:_|$)", name.upper())
    if not m:
        return None
    letters, digits, quad = m.groups()
    l1, l2 = GRID_LETTERS.index(letters[0]), GRID_LETTERS.index(letters[1])
    e = (((l1 - 2) % 5) * 5 + (l2 % 5)) * 100_000
    n = ((19 - (l1 // 5) * 5) - (l2 // 5)) * 100_000
    k = len(digits) // 2
    size = 10 ** (5 - k)
    e, n = e + int(digits[:k]) * size, n + int(digits[k:]) * size
    if quad:
        size //= 2
        e += size if quad[1] == "E" else 0
        n += size if quad[0] == "N" else 0
    return float(e), float(n), float(e + size), float(n + size)


def grid_tiles(geom_bng, size_m: int) -> list[tuple[float, float, float, float]]:
    """Every size_m tile on the OS grid that intersects a geometry in EPSG:27700."""
    w, s, e, n = geom_bng.bounds
    out = []
    for x in range(int(w // size_m) * size_m, int(e) + 1, size_m):
        for y in range(int(s // size_m) * size_m, int(n) + 1, size_m):
            b = (float(x), float(y), float(x + size_m), float(y + size_m))
            if geom_bng.intersects(box(*b)):
                out.append(b)
    return out


def tile_name_5km(b: tuple) -> str:
    """The Environment Agency's 5 km tile id for an SW corner, e.g. (495000, 145000) -> "SU9545"."""
    e, n = int(b[0]), int(b[1])
    l1, l2 = _letters(e // 100_000, n // 100_000)
    return f"{l1}{l2}{(e % 100_000) // 1000:02d}{(n % 100_000) // 1000:02d}"


def _letters(e100: int, n100: int) -> tuple[str, str]:
    for i1, a in enumerate(GRID_LETTERS):
        for i2, b in enumerate(GRID_LETTERS):
            if ((i1 - 2) % 5) * 5 + (i2 % 5) == e100 and (19 - (i1 // 5) * 5) - (i2 // 5) == n100:
                return a, b
    raise ValueError(f"no OS 100 km square at {e100}, {n100}")


# ------------------------------------------------------------------ regions


def to_bng(geom_wgs84):
    return transform(_TO_BNG.transform, geom_wgs84)


def nations() -> dict[str, object]:
    """Nation polygons (WGS84) from the ONS countries file (data/raw/boundary/uk_countries.geojson)."""
    gj = json.loads((RAW / "boundary" / "uk_countries.geojson").read_text())
    out = {}
    for f in gj["features"]:
        name = next(v for k, v in f["properties"].items() if k.upper().startswith("CTRY") and k.upper().endswith("NM"))
        out[name] = shape(f["geometry"])
    return out


def region(name: str | None = None, bbox: str | None = None):
    """A region (WGS84): a nation, a local authority (ONS Dec 2024 names), "UK", or a bbox "w,s,e,n"."""
    if bbox:
        w, s, e, n = (float(v) for v in bbox.split(","))
        return box(w, s, e, n), f"bbox {bbox}"
    ns = nations()
    if name in ns:
        return ns[name], name
    if name and name.upper() in ("UK", "UNITED KINGDOM"):
        return unary_union(list(ns.values())), "United Kingdom"
    gj = json.loads((RAW / "boundary" / "lad_2024.geojson").read_text())
    for f in gj["features"]:
        lad = next(v for k, v in f["properties"].items() if k.upper().startswith("LAD") and k.upper().endswith("NM"))
        if lad.lower() == (name or "").lower():
            return shape(f["geometry"]), lad
    raise LookupError(f"no nation or local authority called {name!r} (or pass a bbox)")


# ------------------------------------------------------------------ England


def england_tiles(geom_bng, res: int) -> list[Tile]:
    out = []
    for b in grid_tiles(geom_bng, 5000):
        name = tile_name_5km(b)
        for layer, product in EA_PRODUCTS.items():
            out.append(Tile("England", f"{product} 2022 {res}m", layer, name, b, EA_TILE_BYTES[res][layer], "sample",
                            f"{EA_BASE}/{product}/2022/{res}/{name}"))
    return out


def england_available(geom_wgs84) -> set[str]:
    """Tile ids the EA catalogue actually has for a (small) region: drops sea tiles from the grid estimate."""
    r = httpx.post(f"{EA_BASE}/search", json=json.loads(json.dumps(geom_wgs84.__geo_interface__)), timeout=TIMEOUT,
                   headers={"content-type": "application/geo+json"})
    r.raise_for_status()
    return {x["tile"]["id"] for x in r.json()["results"] if x["product"]["id"] in EA_PRODUCTS.values()}


# ------------------------------------------------------------------ Wales


def wales_tiles(geom_bng, max_head: int = 300) -> list[Tile]:
    """1 km tiles from the DataMapWales WFS; sizes by HEAD for up to max_head tiles, the rest estimated from them."""
    w, s, e, n = geom_bng.bounds
    params = {"service": "WFS", "version": "2.0.0", "request": "GetFeature", "typeNames": WALES_LAYER,
              "outputFormat": "application/json", "srsName": "EPSG:27700", "bbox": f"{w},{s},{e},{n},EPSG:27700"}
    feats = httpx.get(WALES_WFS, params=params, timeout=TIMEOUT * 3).json()["features"]
    out = []
    for f in feats:
        b = shape(f["geometry"]).bounds
        if not geom_bng.intersects(box(*b)):
            continue
        p = f["properties"]
        for layer in ("dsm", "dtm"):
            out.append(Tile("Wales", "Welsh Government LiDAR 2020-22 1m", layer, p["british_gr"], b, None, "estimated",
                            "https://" + p[f"{layer}_link"].removeprefix("https://")))
    with httpx.Client(timeout=TIMEOUT) as c:
        for t in out[:max_head]:
            r = c.head(t.url)
            if r.status_code == 200 and r.headers.get("content-length"):
                t.size, t.size_how = int(r.headers["content-length"]), "head"
    known = [t.size for t in out if t.size]
    mean = int(sum(known) / len(known)) if known else 2_060_000
    for t in out:
        if t.size is None:
            t.size = mean
    return out


# ------------------------------------------------------------------ Scotland


def scotland_listing(refresh: bool = False) -> list[dict]:
    """Every DSM / DTM GeoTIFF in the bucket: key, size (cached in data/cache/lidar/scotland_listing.json)."""
    path = CACHE / "scotland_listing.json"
    if path.exists() and not refresh:
        return json.loads(path.read_text())
    ns = {"s": "http://s3.amazonaws.com/doc/2006-03-01/"}
    rows, token = [], None
    with httpx.Client(timeout=TIMEOUT) as c:
        while True:
            p = {"list-type": "2", "prefix": "lidar/", "max-keys": "1000", **({"continuation-token": token} if token else {})}
            root = ET.fromstring(c.get(SCOTLAND_BUCKET, params=p).content)
            for o in root.findall("s:Contents", ns):
                key = o.find("s:Key", ns).text
                if key.endswith(".tif") and re.search(r"/(dsm|dtm)/", key):
                    rows.append({"key": key, "size": int(o.find("s:Size", ns).text)})
            if root.find("s:IsTruncated", ns).text != "true":
                break
            token = root.find("s:NextContinuationToken", ns).text
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows))
    return rows


def scotland_collection(key: str) -> str:
    """lidar/phase-3/dsm/... -> "phase-3"; lidar/hes/hes-2016/dsm/... -> "hes/hes-2016"."""
    return re.sub(r"/(dsm|dtm)/.*$", "", key.removeprefix("lidar/"))


def scotland_tiles(geom_bng, collections: list[str] = SCOTLAND_OGL) -> list[Tile]:
    out = []
    for row in scotland_listing():
        col = scotland_collection(row["key"])
        if col not in collections:
            continue
        b = grid_tile_bounds(row["key"].rsplit("/", 1)[-1])
        if b is None or not geom_bng.intersects(box(*b)):
            continue
        layer = "dsm" if "/dsm/" in row["key"] else "dtm"
        out.append(Tile("Scotland", col, layer, row["key"].rsplit("/", 1)[-1], b, row["size"], "listed",
                        f"{SCOTLAND_BUCKET}/{row['key']}"))
    return out


# ------------------------------------------------------------------ Northern Ireland


def ni_resources() -> list[dict]:
    """Every DSM/DTM zip in the River Basin LiDAR datasets: dataset, name, size, url (CKAN API)."""
    r = httpx.get(NI_CKAN, params={"q": "River Basin LIDAR", "rows": 50}, timeout=TIMEOUT,
                  headers={"user-agent": "Mozilla/5.0 (coverage-detective lidar size check)"})
    r.raise_for_status()
    out = []
    for p in r.json()["result"]["results"]:
        if "LIDAR" not in p["title"].upper():
            continue
        for res in p["resources"]:
            fmt, url = (res.get("format") or "").upper(), res.get("url") or ""
            if fmt in ("ZIP", "ASCII GRID") and "extents" not in url.lower() and "grid_index" not in url.lower():
                out.append({"dataset": p["title"], "name": res.get("name") or url.rsplit("/", 1)[-1],
                            "size": int(res["size"]) if res.get("size") else None, "url": url,
                            "point_cloud": "point-cloud" in url or "point_cloud" in url})
    return out


def ni_tiles(geom_wgs84=None) -> list[Tile]:
    """NI zips (DSM + DTM in one zip). With a region, only zips whose survey area intersects it (needs the extents
    shapefile, 2 MB, kept in data/raw/lidar_index/)."""
    res = [r for r in ni_resources() if not r["point_cloud"]]
    if geom_wgs84 is not None:
        areas = ni_extents()
        keep = {name.lower() for name, g in areas.items() if g.intersects(geom_wgs84)}
        res = [r for r in res if _ni_area(r["name"]) in keep]
    out = [Tile("Northern Ireland", r["dataset"], "dsm+dtm", r["name"], None, r["size"],
                "listed" if r["size"] else "estimated", r["url"]) for r in res]
    with httpx.Client(timeout=TIMEOUT, follow_redirects=True) as c:
        for t in out:
            if t.size is None:
                h = c.head(t.url)
                if h.status_code == 200 and h.headers.get("content-length"):
                    t.size, t.size_how = int(h.headers["content-length"]), "head"
    return out


def _ni_area(name: str) -> str:
    return re.sub(r"[_ ]\d{2}[_ ]\d{2}[_ ]\d{4}.*$", "", name).replace("_", " ").strip().lower()


def ni_extents() -> dict[str, object]:
    """Survey area name -> polygon (WGS84), from the OpenDataNI extents shapefile (Irish Grid)."""
    import geopandas as gpd

    path = RAW / "lidar_index" / "RiverBasin_LIDAR_Extents.zip"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(httpx.get(NI_EXTENTS, timeout=TIMEOUT).content)
    gdf = gpd.read_file(f"zip://{path}")
    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:29903")
    gdf = gdf.to_crs(4326)
    col = next(c for c in gdf.columns if c.lower() in ("name", "area", "area_name", "lidar_name", "location")) \
        if any(c.lower() in ("name", "area", "area_name", "lidar_name", "location") for c in gdf.columns) else gdf.columns[0]
    return {str(r[col]): r.geometry for _, r in gdf.iterrows()}


def polygon_area_km2(geom_bng: Polygon) -> float:
    return geom_bng.area / 1e6


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1000 or unit == "TB":
            return f"{n:,.1f} {unit}" if unit != "B" else f"{n:,.0f} B"
        n /= 1000
    return str(n)


def transfer_hours(nbytes: float, mbit_s: float) -> float:
    return nbytes * 8 / (mbit_s * 1e6) / 3600
