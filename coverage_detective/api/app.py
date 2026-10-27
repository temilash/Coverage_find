"""Data explorer API and web app.

    python run.py                     # checks Zetaris, then serves on http://localhost:8000

Routes:
  GET /health                  server, layers and Zetaris status
  GET /api/layers              map layers (from data/web/layers/index.json)
  GET /api/layer/{name}        [cell_id, value(, weak)] for every cell with a value
  GET /api/masts?bbox=         approximate mast sites in a bbox (max 5,000)
  GET /api/tiles?bbox=         Ookla tiles (latest quarter) in a small bbox
  GET /api/cell/{cell_id}      everything about one cell, via one Zetaris query
  GET /api/geocode?q=          postcode or place name (postcodes.io)
  GET /api/geocode/reverse     nearest postcode to ?lat=&lon=
  GET /api/live?lat=&lon=      live context: weather, Met Office warnings, EA flood warnings
  GET /api/los?lat=&lon=(&site_id=)  line of sight from the exact point to a site (default nearest)
  POST /api/evidence           evidence bundle for {"query": postcode or place} or {"lat", "lon"}
  POST /api/diagnose           start an agent diagnosis -> run_id (api/agents.py; also zone runs,
  GET  /api/runs/{run_id}      letters and the council priority summary)
  POST /run                    one call: {lat, lon} | {place} | {zone} -> waits, returns the diagnosis as JSON
  /                            the data explorer (web/index.html)
  /signal                      My signal: evidence for one exact point (web/signal.html)
  /council                     Council view: ranked gap zones (web/council.html; API in api/council.py)
"""

from __future__ import annotations

import json
import math
import re
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

import h3
import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field, model_validator
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from coverage_detective.config import H3_RES, PROCESSED, ROOT, SAMPLES, sample_mode
from coverage_detective import evidence as evidence_bundle
from coverage_detective.api.agents import router as agents_router, run_router
from coverage_detective.api.council import router as council_router
from coverage_detective.config import UK_BBOX
from coverage_detective.data import manifest, sample_db, terrain
from coverage_detective.data.queries import cell_sql
from coverage_detective.data.zetaris import QUERY_LOG, ZetarisError, explain, query
from coverage_detective.data.zetaris import status as zetaris_status
from coverage_detective.sources import flood_warnings, geocode as geocoder, live as live_sources
from coverage_detective.sources import met_office_warnings, weather

LAYERS_DIR = ROOT / "data" / "web" / "layers"
WEB_DIR = ROOT / "web"
MAX_MASTS = 5000
MAX_TILE_BBOX_DEG2 = 0.6  # tiles only when zoomed in

app = FastAPI(title="Coverage Detective data explorer")
app.include_router(council_router)
app.include_router(agents_router)
app.include_router(run_router)


# --- static data held in memory ------------------------------------------------------------


def _data_file(name: str):
    """data/processed/<name>, or in sample mode the committed extract (sample_data/zetaris/<name>)."""
    return SAMPLES / "zetaris" / name if sample_mode() else PROCESSED / name


@lru_cache(maxsize=1)
def mast_sites() -> pd.DataFrame:
    cols = ["site_id", "lat", "lon", "sources", "operators", "operators_inferred", "operators_confirmed",
            "operator_labels", "radio_types", "cell_count", "osm_operator", "mobile_tagged",
            "has_microwave_backhaul", "antenna_height_m", "wtr_links"]
    return pd.read_parquet(_data_file("masts_sites.parquet"), columns=cols)


def _quadkey_bounds(qk: str) -> tuple[float, float, float, float]:
    x = y = 0
    for ch in qk:
        d = int(ch)
        x, y = (x << 1) | (d & 1), (y << 1) | (d >> 1)
    n = 2 ** len(qk)
    lat = lambda yy: math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * yy / n))))  # noqa: E731
    return x / n * 360 - 180, lat(y + 1), (x + 1) / n * 360 - 180, lat(y)


@lru_cache(maxsize=1)
def latest_tiles() -> pd.DataFrame:
    tiles = pd.read_parquet(_data_file("ookla_mobile_tiles.parquet"))
    latest = tiles[tiles.quarter == tiles.quarter.max()].reset_index(drop=True)
    bounds = [_quadkey_bounds(q) for q in latest.quadkey]
    latest[["min_lon", "min_lat", "max_lon", "max_lat"]] = pd.DataFrame(bounds)
    return latest


def _bbox(bbox: str) -> tuple[float, float, float, float]:
    try:
        w, s, e, n = (float(v) for v in bbox.split(","))
    except ValueError as err:
        raise HTTPException(400, "bbox must be minLon,minLat,maxLon,maxLat") from err
    return w, s, e, n


# --- routes ---------------------------------------------------------------------------------


@app.get("/health")
def health() -> dict:
    zetaris = zetaris_status()
    layers = len(json.loads((LAYERS_DIR / "index.json").read_text())) if (LAYERS_DIR / "index.json").exists() else 0
    return {"status": "ok", "layers": layers, "zetaris": zetaris, "sample_mode": sample_mode()}


@app.get("/api/layers")
def layers() -> list[dict]:
    path = LAYERS_DIR / "index.json"
    if not path.exists():
        raise HTTPException(503, "No layers built; run scripts/process/build_web_layers.py")
    return json.loads(path.read_text())


@app.get("/api/layer/{name}")
def layer(name: str) -> FileResponse:
    if not re.fullmatch(r"[a-z0-9_]+", name) or not (LAYERS_DIR / f"{name}.json").exists():
        raise HTTPException(404, f"No layer {name!r}")
    return FileResponse(LAYERS_DIR / f"{name}.json", media_type="application/json",
                        headers={"Cache-Control": "public, max-age=3600"})


@app.get("/api/masts")
def masts(bbox: str = Query(..., description="minLon,minLat,maxLon,maxLat")) -> dict:
    w, s, e, n = _bbox(bbox)
    df = mast_sites()
    inside = df[df.lon.between(w, e) & df.lat.between(s, n)]
    truncated = len(inside) > MAX_MASTS
    inside = inside.sort_values("cell_count", ascending=False).head(MAX_MASTS)
    sites = json.loads(inside.to_json(orient="records"))
    return {"count": len(sites), "truncated": truncated, "sites": sites,
            **manifest.evidence_meta("masts_sites")}


@app.get("/api/tiles")
def tiles(bbox: str = Query(..., description="minLon,minLat,maxLon,maxLat")) -> dict:
    w, s, e, n = _bbox(bbox)
    if (e - w) * (n - s) > MAX_TILE_BBOX_DEG2:
        return {"count": 0, "tiles": [], "note": "zoom in to see Ookla tiles"}
    df = latest_tiles()
    inside = df[(df.max_lon >= w) & (df.min_lon <= e) & (df.max_lat >= s) & (df.min_lat <= n)]
    cols = ["quadkey", "min_lon", "min_lat", "max_lon", "max_lat", "download_mbps", "upload_mbps",
            "latency_ms", "tests", "devices"]
    return {"count": len(inside), "quarter": str(df.quarter.iloc[0]) if len(df) else None,
            "tiles": json.loads(inside[cols].round(4).to_json(orient="records"))}


@app.get("/api/cell/{cell_id}")
def cell(cell_id: str) -> dict:
    if not h3.is_valid_cell(cell_id) or h3.get_resolution(cell_id) != H3_RES:
        raise HTTPException(400, f"{cell_id!r} is not an H3 resolution-{H3_RES} cell")
    sql, tables = cell_sql(cell_id)
    try:
        rows = query(sql, timeout=60)
    except ZetarisError as e:
        raise HTTPException(502, f"Zetaris query failed: {explain(e)}") from e
    if not rows:
        raise HTTPException(404, f"Cell {cell_id} is not in the UK grid")
    data = rows[0]
    history = json.loads(data.pop("history_json") or "[]")
    data["history"] = sorted(history, key=lambda h: h["quarter"])
    data["place"] = geocoder.place_name(geocoder.reverse(data["lat"], data["lon"]).data["nearest"])
    data["neighbours"] = sorted(h3.grid_ring(cell_id, 1))
    log = QUERY_LOG[-1]
    data_dates = {}
    for t in tables:
        try:
            data_dates[t] = manifest.evidence_meta(t)
        except KeyError:
            pass
    return {"cell": data, "zetaris": {"sql": sql, "duration_ms": log["duration_ms"], "rows": log["rows"]},
            "data_dates": data_dates}


@app.get("/api/geocode")
def geocode(q: str = Query(..., min_length=2)) -> dict:
    res = geocoder.lookup(q)
    if res.status == "unavailable":
        raise HTTPException(502, f"postcodes.io lookup failed: {res.error}")
    return {**res.data, "source": res.to_dict()}


@app.get("/api/geocode/reverse")
def geocode_reverse(lat: float = Query(..., ge=-90, le=90), lon: float = Query(..., ge=-180, le=180)) -> dict:
    res = geocoder.reverse(lat, lon)
    if res.status == "unavailable":
        raise HTTPException(502, f"postcodes.io lookup failed: {res.error}")
    return {**res.data, "place": geocoder.place_name(res.data["nearest"]), "source": res.to_dict()}


@app.get("/api/los")
def los(lat: float = Query(..., ge=49, le=61.5), lon: float = Query(..., ge=-9, le=2.5),
        site_id: str | None = Query(None, max_length=40, description="default: the nearest site to the point")) -> dict:
    """Line of sight from the exact point to a site (by default the nearest approximate site)."""
    sites = mast_sites()
    if site_id is None:
        s = sites.iloc[int(terrain.distance_m(lat, lon, sites.lat.to_numpy(), sites.lon.to_numpy()).argmin())]
        site_id = s.site_id
    else:
        hit = sites[sites.site_id == site_id]
        if hit.empty:
            raise HTTPException(404, f"No site {site_id!r}")
        s = hit.iloc[0]
    height = s.antenna_height_m if pd.notna(s.antenna_height_m) else None
    result = terrain.line_of_sight(lat, lon, float(s.lat), float(s.lon), mast_height=height)
    site = {"site_id": site_id, "lat": float(s.lat), "lon": float(s.lon), "sources": s.sources,
            "operators": s.operators or None}
    return {"lat": lat, "lon": lon, "site": site, **result}


class EvidenceRequest(BaseModel):
    query: str | None = Field(None, min_length=2, max_length=100, description="UK postcode or place name")
    lat: float | None = Field(None, ge=UK_BBOX["min_lat"], le=UK_BBOX["max_lat"])
    lon: float | None = Field(None, ge=UK_BBOX["min_lon"], le=UK_BBOX["max_lon"])

    @model_validator(mode="after")
    def one_location(self):
        if not self.query and (self.lat is None or self.lon is None):
            raise ValueError('give "query", or both "lat" and "lon"')
        return self


@app.post("/api/evidence")
def evidence(req: EvidenceRequest) -> dict:
    """Evidence bundle for a point: Zetaris queries (logged in the bundle), line of sight, live sources."""
    if sample_mode() and req.lat is not None and not sample_db.covers(req.lat, req.lon):
        raise HTTPException(404, sample_db.coverage_message())
    try:
        return evidence_bundle.build_bundle(req.query, req.lat, req.lon)
    except LookupError as e:
        raise HTTPException(404, str(e) + " (sample mode: search works for the example places only, e.g. Great Langdale, WC2N 5DU, Rhayader, Sutton St Edmund)" if sample_mode() else "") from e


def live_context(lat: float, lon: float, flood_via: str = "api") -> dict:
    """Weather, Met Office warnings and EA flood warnings for a point, fetched in parallel."""
    place_res = geocoder.reverse(lat, lon)
    place = place_res.data.get("nearest")
    with ThreadPoolExecutor(max_workers=3) as pool:
        w = pool.submit(weather.forecast, lat, lon)
        m = pool.submit(met_office_warnings.for_place, place, lat, lon)
        f = pool.submit(flood_warnings.near, lat, lon, place and place.get("country"), flood_via)
        w, m, f = w.result(), m.result(), f.result()
    calm = (w.data.get("severity") == "none" and m.data.get("count") == 0 and f.data.get("count") in (0, None)
            and "unavailable" not in (w.status, m.status, f.status))
    return {
        "lat": lat, "lon": lon, "cell_id": h3.latlng_to_cell(lat, lon, H3_RES),
        "place": {"name": geocoder.place_name(place), "postcode": place and place.get("postcode"),
                  "distance_m": place_res.data.get("distance_m"), "country": place and place.get("country"),
                  "source": place_res.to_dict()},
        "weather": w.to_dict(), "met_office_warnings": m.to_dict(), "flood_warnings": f.to_dict(),
        "all_calm": calm, "sample_mode": live_sources.sample_mode(), "fetched_at": live_sources.now_iso(),
    }


@app.get("/api/live")
def live(lat: float = Query(..., ge=49, le=61.5), lon: float = Query(..., ge=-9, le=2.5),
         flood_via: str = Query("api", pattern="^(api|zetaris)$")) -> dict:
    return live_context(lat, lon, flood_via)


@app.get("/signal", include_in_schema=False)
def signal_page() -> FileResponse:
    return FileResponse(WEB_DIR / "signal.html")


@app.get("/council", include_in_schema=False)
def council_page() -> FileResponse:
    return FileResponse(WEB_DIR / "council.html")


if WEB_DIR.exists():
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
