"""Estimated coverage (RSRP) per H3 cell for a region, from the radio model (coverage_detective/radio/).

    python scripts/radio/build_coverage.py --region Surrey --estimate      # pairs, samples and time only
    python scripts/radio/build_coverage.py --region Surrey
    python scripts/radio/build_coverage.py --region Guildford --rx-height 30
    python scripts/radio/build_coverage.py --bbox -0.6,51.2,-0.5,51.3 --res 8

Region: "UK", a nation, a local authority (ONS 2024 name), a county made of districts (Surrey), or --bbox.
Every build first estimates the work (site-cell pairs x profile samples) and times a sample of it; a run
estimated at more than --max-hours stops unless --yes is passed.

Inputs: cells_r7, masts_sites (data/processed/), the DEM tiles (data/raw/dem/); optional --clutter, a
Parquet of cell_id, median_height_m, building_fraction per H3 cell (obstruction input; default none).
Writes data/processed/radio_coverage_r<res>.parquet (receiver 1.5 m) or radio_coverage_r<res>_rx<h>m.parquet,
replacing the region's cells and keeping other regions. data/processed/radio_config.json records the config.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from coverage_detective.config import CELLS_PATH, PROCESSED  # noqa: E402
from coverage_detective.data import manifest, terrain  # noqa: E402
from coverage_detective.data.regions import region  # noqa: E402
from coverage_detective.radio import coverage, profile  # noqa: E402
from coverage_detective.radio.config import DEFAULT, RadioConfig  # noqa: E402

SITES_PATH = PROCESSED / "masts_sites.parquet"
TIMING_SAMPLES = 3_000_000   # profile samples timed before the run


def out_path(res: int, rx_height: float) -> Path:
    suffix = "" if rx_height == DEFAULT.rx_height_m else f"_rx{rx_height:g}m"
    return PROCESSED / f"radio_coverage_r{res}{suffix}.parquet"


def timed_sample(cells, sites, cfg, source, obstruction) -> float:
    """Seconds per profile sample, from a random subset of the region's cells."""
    rng = np.random.default_rng(0)
    est = coverage.estimate(cells, sites, cfg)
    per_cell = est["samples"] / max(len(cells), 1)
    n = int(min(len(cells), max(1, TIMING_SAMPLES // max(per_cell, 1))))
    sub = cells.iloc[rng.choice(len(cells), n, replace=False)].reset_index(drop=True)
    sub_est = coverage.estimate(sub, sites, cfg)
    t0 = time.time()
    coverage.coverage(sub, sites, cfg, source, obstruction)
    return (time.time() - t0) / max(sub_est["samples"], 1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--region", help="UK, a nation, a county (Surrey) or a local authority")
    parser.add_argument("--bbox", help="west,south,east,north (WGS84)")
    parser.add_argument("--res", type=int, default=7, help="H3 resolution (7 or finer; default 7)")
    parser.add_argument("--rx-height", type=float, default=DEFAULT.rx_height_m, help="receiver height above ground, m")
    parser.add_argument("--clutter", type=Path, help="per-cell obstruction summary (Parquet)")
    parser.add_argument("--estimate", action="store_true", help="estimate the work and time, then stop")
    parser.add_argument("--max-hours", type=float, default=1.0, help="stop if the estimate is longer (default 1)")
    parser.add_argument("--yes", action="store_true", help="run even if the estimate exceeds --max-hours")
    args = parser.parse_args()
    if not (args.region or args.bbox):
        parser.error("pass --region or --bbox")
    if not any(terrain.DEM_DIR.glob("*.tif")):
        sys.exit(f"No DEM tiles in {terrain.DEM_DIR}; run scripts/download/dem.py")

    cfg = RadioConfig(rx_height_m=args.rx_height)
    geom, label = region(args.region, args.bbox)
    cells = coverage.region_cells(geom, pd.read_parquet(CELLS_PATH), args.res, cfg)
    sites = pd.read_parquet(SITES_PATH, columns=["site_id", "lat", "lon", "operators", "antenna_height_m"])
    source = terrain.DEM(max_tiles=8)
    obstruction = profile.CellClutter(pd.read_parquet(args.clutter)) if args.clutter else None

    est = coverage.estimate(cells, sites, cfg)
    unknown = int((sites.operators.fillna("") == "").sum())
    print(f"{label}: {est['cells']:,} cells at H3 r{args.res}, receiver {cfg.rx_height_m:g} m")
    for g in est["groups"]:
        print(f"  {g['freq_mhz']:g} MHz ({', '.join(g['operators'])}), range {g['max_range_km']:g} km: "
              f"{g['sites']:,} sites, {g['pairs']:,} site-cell pairs, {g['samples'] / 1e6:,.0f} m profile samples")
    print(f"  {unknown:,} UK sites with no known operator are not modelled")
    per_sample = timed_sample(cells, sites, cfg, source, obstruction)
    hours = est["samples"] * per_sample / 3600
    print(f"  timed: {per_sample * 1e9:.0f} ns per profile sample -> about {hours * 60:.0f} min for the region")
    if args.estimate:
        return
    if hours > args.max_hours and not args.yes:
        sys.exit(f"Estimated {hours:.1f} h, more than --max-hours {args.max_hours:g}: pass --yes to run it.")

    t0 = time.time()
    last = [0.0]

    def progress(done, total, secs):
        if time.time() - last[0] > 30:
            last[0] = time.time()
            print(f"    {done:,}/{total:,} pairs  {secs / 60:.1f} min")

    out = coverage.coverage(cells, sites, cfg, source, obstruction, progress=progress, region=label)
    path = out_path(args.res, cfg.rx_height_m)
    if path.exists():
        old = pd.read_parquet(path)
        out = pd.concat([old[~old.cell_id.isin(out.cell_id)], out], ignore_index=True)
    out = out.sort_values("cell_id").reset_index(drop=True)
    out.to_parquet(path, index=False)
    (PROCESSED / "radio_config.json").write_text(json.dumps(cfg.as_dict(), indent=1))
    done = out[out.region == label]
    print(f"  wrote {path.name}: {len(done):,} cells for {label} ({len(out):,} in all) in {(time.time() - t0) / 60:.1f} min; "
          f"best-server RSRP median {done.best_rsrp_dbm.median():.0f} dBm, "
          f"{done.best_rsrp_dbm.isna().sum():,} cells with no site in range")
    manifest.record(
        "radio_coverage",
        title="Radio model: estimated best-server RSRP per H3 cell (Hata / COST-231 Hata + Bullington diffraction)",
        data_period=f"model {cfg.as_dict()['model_version']}; regions: "
                    + ", ".join(sorted(out.region.dropna().unique())),
        downloaded={},
        outputs=[path],
        refresh="after the mast sites change",
        licence="derived from OpenCelliD (CC BY-SA 4.0), OpenStreetMap (ODbL), Ofcom WTR and Copernicus DEM inputs",
    )


if __name__ == "__main__":
    main()
