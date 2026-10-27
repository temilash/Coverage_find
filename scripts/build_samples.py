"""Extracts for sample mode (python run.py --sample): small copies of every Zetaris source and DEM crops
around the example places and zones, so the agents run with no Zetaris, no live calls and no LLM key.

    python scripts/build_samples.py

Reads data/processed/*.parquet (the files Zetaris serves) and data/raw/dem/; writes
sample_data/zetaris/<table>.parquet + extract.json (tables, boxes, row counts), uk_context.json (the
UK-wide medians, from the full data) and sample_data/dem/<box>.tif (int16, deflate, native 30 m).
Boxes: 12 km around each sample place (sites within 10 km and line of sight to them), and the zone's
cells + 16 km for each example zone (candidate high points within 6.5 km, reach 8 km). Zone tables
keep the example zones and the zones that contain a sample place. Re-run after a data rebuild.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from coverage_detective.config import CELLS_PATH, PROCESSED, RAW, SAMPLE_LOCATIONS, SAMPLES  # noqa: E402

OUT = SAMPLES / "zetaris"
DEM_OUT = SAMPLES / "dem"
POINT_KM = 12.0
ZONE_KM = 16.0
EXAMPLE_ZONES = ["Z00072", "O00332", "Z00023", "O00004"]  # Ousby, Broadstone (gap zones); Windermere, Ludlow (opportunities)
TABLES = [  # (database, table, file, how to cut it)
    ("CD_CELLS", "cells_r7", CELLS_PATH, "cell"),
    ("CD_OOKLA_MOBILE", "ookla_mobile_r7", PROCESSED / "ookla_mobile_r7.parquet", "cell_id"),
    ("CD_OOKLA_MOBILE_HISTORY", "ookla_mobile_history_r7", PROCESSED / "ookla_mobile_history_r7.parquet", "cell_id"),
    ("CD_OOKLA_MOBILE_TILES", "ookla_mobile_tiles", PROCESSED / "ookla_mobile_tiles.parquet", "latlon"),
    ("CD_MASTS_SITES", "masts_sites", PROCESSED / "masts_sites.parquet", "latlon"),
    ("CD_MASTS", "masts_r7", PROCESSED / "masts_r7.parquet", "cell_id"),
    ("CD_OFCOM_AREA", "ofcom_area_r7", PROCESSED / "ofcom_area_r7.parquet", "cell_id"),
    ("CD_OFCOM_NATIONS", "ofcom_nations", PROCESSED / "ofcom_nations.parquet", "all"),
    ("CD_TERRAIN", "terrain_r7", PROCESSED / "terrain_r7.parquet", "cell_id"),
    ("CD_LOS", "los_r7", PROCESSED / "los_r7.parquet", "cell_id"),
    ("CD_GAP_ZONES", "gap_zones", PROCESSED / "gap_zones.parquet", "zone"),
    ("CD_GAP_ZONE_CELLS", "gap_zone_cells", PROCESSED / "gap_zone_cells.parquet", "zone"),
    ("CD_OPP_ZONES", "opportunity_zones", PROCESSED / "opportunity_zones.parquet", "zone"),
    ("CD_OPP_ZONE_CELLS", "opportunity_zone_cells", PROCESSED / "opportunity_zone_cells.parquet", "zone"),
]


def box(lat_min, lat_max, lon_min, lon_max, km) -> list[float]:
    dlat = km / 111.0
    dlon = km / (111.32 * math.cos(math.radians((lat_min + lat_max) / 2)))
    return [round(lon_min - dlon, 4), round(lat_min - dlat, 4), round(lon_max + dlon, 4), round(lat_max + dlat, 4)]


def in_boxes(lat, lon, boxes) -> np.ndarray:
    m = np.zeros(len(lat), dtype=bool)
    for w, s, e, n in boxes.values():
        m |= (lon >= w) & (lon <= e) & (lat >= s) & (lat <= n)
    return m


def dem_crop(name: str, b: list[float]) -> int:
    """Write the DEM inside a box (native resolution, int16 metres) and return the file size."""
    import rasterio
    from rasterio.merge import merge

    from coverage_detective.data.terrain import tile_name

    w, s, e, n = b
    paths = [RAW / "dem" / f"{tile_name(la, lo)}.tif" for la in range(math.floor(s), math.floor(n) + 1)
             for lo in range(math.floor(w), math.floor(e) + 1)]
    srcs = [rasterio.open(p) for p in paths if p.exists()]
    arr, transform = merge(srcs, bounds=(w, s, e, n), nodata=0)
    profile = {"driver": "GTiff", "height": arr.shape[1], "width": arr.shape[2], "count": 1, "dtype": "int16",
               "crs": srcs[0].crs, "transform": transform, "compress": "deflate", "predictor": 2, "tiled": True}
    for src in srcs:
        src.close()
    path = DEM_OUT / f"{name}.tif"
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(np.round(arr[0]).astype(np.int16), 1)
    return path.stat().st_size


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    DEM_OUT.mkdir(parents=True, exist_ok=True)
    cells = pd.read_parquet(CELLS_PATH, columns=["cell_id", "lat", "lon"])
    boxes: dict[str, list[float]] = {}
    sample_cells = set()
    import h3
    for name, lat, lon in SAMPLE_LOCATIONS:
        slug = name.split(",")[0].split("(")[0].strip().lower().replace(" ", "_")
        boxes[slug] = box(lat, lat, lon, lon, POINT_KM)
        sample_cells.add(h3.latlng_to_cell(lat, lon, 7))
    zone_ids = set(EXAMPLE_ZONES)
    for db, table, path, how in TABLES:
        if table.endswith("zone_cells"):
            zc = pd.read_parquet(path, columns=["zone_id", "cell_id"])
            zone_ids |= set(zc[zc.cell_id.isin(sample_cells)].zone_id)
    for kind_cells in ("gap_zone_cells", "opportunity_zone_cells"):
        zc = pd.read_parquet(PROCESSED / f"{kind_cells}.parquet", columns=["zone_id", "lat", "lon"])
        for zid, g in zc[zc.zone_id.isin(zone_ids)].groupby("zone_id"):
            boxes[f"zone_{zid}"] = box(g.lat.min(), g.lat.max(), g.lon.min(), g.lon.max(), ZONE_KM)
    keep_cells = set(cells.cell_id[in_boxes(cells.lat.to_numpy(), cells.lon.to_numpy(), boxes)])
    print(f"  {len(boxes)} boxes, {len(keep_cells):,} cells, zones {sorted(zone_ids)}")
    out_tables = []
    for db, table, path, how in TABLES:
        df = pd.read_parquet(path)
        if how == "cell":
            df = df[df.cell_id.isin(keep_cells)]
        elif how == "cell_id":
            df = df[df.cell_id.isin(keep_cells)]
        elif how == "latlon":
            df = df[in_boxes(df.lat.to_numpy(), df.lon.to_numpy(), boxes)]
        elif how == "zone":
            df = df[df.zone_id.isin(zone_ids)]
        file = f"{table}.parquet"
        df.to_parquet(OUT / file, index=False)
        out_tables.append({"database": db, "table": table, "file": file, "rows": len(df)})
        print(f"  {db}.{table}: {len(df):,} rows")
    # The UK-wide medians come from the full data, so sample answers match full answers.
    o = pd.read_parquet(PROCESSED / "ookla_mobile_r7.parquet", columns=["devices_per_1k_pop", "download_mbps", "tests"])
    o = o[o.tests >= 10]
    (OUT / "uk_context.json").write_text(json.dumps([{
        "devices_per_1k_p50": float(o.devices_per_1k_pop.quantile(0.5)), "devices_per_1k_p90": float(o.devices_per_1k_pop.quantile(0.9)),
        "download_mbps_p50": float(o.download_mbps.quantile(0.5)), "cells": int(len(o))}]))
    sizes = {name: dem_crop(name, b) for name, b in boxes.items()}
    (OUT / "extract.json").write_text(json.dumps({"tables": out_tables, "boxes": boxes, "zones": sorted(zone_ids),
                                                  "dem_bytes": sizes}, indent=1))
    total = sum(p.stat().st_size for p in OUT.iterdir()) + sum(sizes.values())
    print(f"  DEM crops: {sum(sizes.values()) / 1e6:.1f} MB; total sample extracts {total / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
