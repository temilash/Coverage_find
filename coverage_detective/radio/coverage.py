"""Estimated coverage per H3 cell: RSRP from every approximate mast site within range, best server overall
and per operator.

    cells = region_cells(geometry, res=7)              # cell_id, lat, lon, density, environment
    out = coverage(cells, sites)                       # one row per cell

The receiver is at the cell centre, cfg.rx_height_m above ground. Each site is modelled for every operator it
is labelled with (masts_sites.operators: confirmed by Ofcom's register or inferred from crowd-sourced data),
on that operator's band from the config. Sites whose operator is unknown are left out (no band to model).
Path loss is computed once per site and cell for each band; RSRP = reference-signal EIRP + antenna pattern
gain + receiver gain - path loss.
"""

from __future__ import annotations

import time
from collections import defaultdict
from datetime import datetime, timezone

import h3
import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

from coverage_detective.data.masts import _km_to_chord, _unit_vectors, bearing_deg
from coverage_detective.data.terrain import distance_m
from coverage_detective.radio import profile, propagation
from coverage_detective.radio.config import DEFAULT, MODEL_VERSION, OPERATORS, RadioConfig

BATCH_SAMPLES = 2_000_000   # profile samples per batch (~150 MB of working arrays)


def environment(density, cfg: RadioConfig = DEFAULT) -> np.ndarray:
    d = np.asarray(density, dtype=float)
    return np.select([d >= cfg.urban_density, d >= cfg.suburban_density], ["urban", "suburban"], "open")


def region_cells(geom_wgs84, cells_r7: pd.DataFrame, res: int = 7, cfg: RadioConfig = DEFAULT) -> pd.DataFrame:
    """H3 cells whose centre lies in the region, with people per km2 of their r7 parent (cells_r7)."""
    ids = sorted(h3.geo_to_cells(geom_wgs84, res))
    pop = cells_r7.set_index("cell_id").population
    parents = [h3.cell_to_parent(c, 7) if res > 7 else c for c in ids]
    if res < 7:
        raise ValueError("resolution 7 or finer (coarser roll-ups come from the r7 results)")
    density = np.array([pop.get(p, 0.0) / h3.cell_area(p, "km^2") for p in parents])
    lat, lon = np.array([h3.cell_to_latlng(c) for c in ids]).T if ids else (np.array([]), np.array([]))
    return pd.DataFrame({"cell_id": ids, "lat": lat, "lon": lon, "density": density,
                         "environment": environment(density, cfg)})


def site_operators(sites: pd.DataFrame, cfg: RadioConfig = DEFAULT) -> pd.DataFrame:
    """One row per (site, operator) for the modelled operators, with the antenna height and its source."""
    rows = []
    for s in sites.itertuples(index=False):
        ops = [o for o in str(s.operators or "").split(",") if o in cfg.operator_band]
        if not ops:
            continue
        height, how = cfg.site_height(s.site_id, getattr(s, "antenna_height_m", None))
        rows += [(s.site_id, s.lat, s.lon, op, height, how) for op in ops]
    return pd.DataFrame(rows, columns=["site_id", "lat", "lon", "operator", "height_m", "height_source"])


def _band_groups(cfg: RadioConfig) -> dict[tuple[float, float], list[str]]:
    """Operators sharing a frequency and range share one path-loss computation."""
    groups = defaultdict(list)
    for op in cfg.operator_band:
        b = cfg.band(op)
        groups[(b.freq_mhz, b.max_range_km)].append(op)
    return dict(groups)


def pairs_within(cells: pd.DataFrame, sites: pd.DataFrame, max_km: float) -> tuple[np.ndarray, np.ndarray]:
    """(cell index, site index) for every pair at most max_km apart."""
    if not len(cells) or not len(sites):
        return np.array([], dtype=np.int64), np.array([], dtype=np.int64)
    tree = cKDTree(_unit_vectors(sites.lat, sites.lon))
    hits = tree.query_ball_point(_unit_vectors(cells.lat, cells.lon), r=_km_to_chord(max_km))
    ci = np.repeat(np.arange(len(cells)), [len(h) for h in hits])
    si = np.fromiter((j for h in hits for j in h), dtype=np.int64, count=len(ci))
    return ci, si


def estimate(cells: pd.DataFrame, sites: pd.DataFrame, cfg: RadioConfig = DEFAULT) -> dict:
    """Pairs and profile samples the build would compute (no terrain read)."""
    ops = site_operators(sites, cfg)
    out = {"cells": len(cells), "pairs": 0, "samples": 0, "groups": []}
    for (freq, rng), group_ops in _band_groups(cfg).items():
        s = ops[ops.operator.isin(group_ops)].drop_duplicates("site_id").reset_index(drop=True)
        ci, si = pairs_within(cells, s, rng)
        length = distance_m(cells.lat.to_numpy()[ci], cells.lon.to_numpy()[ci], s.lat.to_numpy()[si],
                            s.lon.to_numpy()[si])
        samples = int(profile.sample_counts(length, cfg.sample_spacing_m).sum())
        out["groups"].append({"freq_mhz": freq, "max_range_km": rng, "operators": group_ops,
                              "sites": len(np.unique(si)),
                              "pairs": len(ci), "samples": samples})
        out["pairs"] += len(ci)
        out["samples"] += samples
    return out


def _path_losses(cells, s, ci, si, freq, cfg, source, obstruction, batch_samples, progress):
    """Path loss, diffraction loss and distance for each (cell, site) pair, in batches of profile samples."""
    length = distance_m(cells.lat.to_numpy()[ci], cells.lon.to_numpy()[ci], s.lat.to_numpy()[si],
                        s.lon.to_numpy()[si])
    counts = profile.sample_counts(length, cfg.sample_spacing_m)
    # Order pairs by site position so each batch reads few DEM tiles.
    order = np.lexsort((s.lon.to_numpy()[si], np.floor(s.lat.to_numpy()[si])))
    batch = (np.cumsum(counts[order]) - 1) // batch_samples
    loss, diff = np.empty(len(ci)), np.empty(len(ci))
    t0, done = time.time(), 0
    for idx in np.split(order, np.flatnonzero(np.diff(batch)) + 1):
        c, t = ci[idx], si[idx]
        p = profile.build(s.lat.to_numpy()[t], s.lon.to_numpy()[t], cells.lat.to_numpy()[c],
                          cells.lon.to_numpy()[c], source, cfg.sample_spacing_m, obstruction)
        hb = s.height_m.to_numpy()[t]
        loss[idx], diff[idx] = propagation.path_loss_db(
            p, p.ground_tx + hb, p.ground_rx + cfg.rx_height_m, freq, hb, cfg.rx_height_m,
            cells.environment.to_numpy()[c], cfg.k_factor)
        done += len(idx)
        if progress:
            progress(done, len(ci), time.time() - t0)
    return loss, diff, length


def coverage(cells: pd.DataFrame, sites: pd.DataFrame, cfg: RadioConfig = DEFAULT, source=None, obstruction=None,
             batch_samples: int = BATCH_SAMPLES, progress=None, region: str = "") -> pd.DataFrame:
    """Best-server RSRP per cell, overall and per operator (NaN where no site of that operator is in range)."""
    if source is None:
        from coverage_detective.data.terrain import dem
        source = dem()
    ops = site_operators(sites, cfg)
    per_pair = []
    for (freq, rng), group_ops in _band_groups(cfg).items():
        s_ops = ops[ops.operator.isin(group_ops)]
        s = s_ops.drop_duplicates("site_id").reset_index(drop=True)
        ci, si = pairs_within(cells, s, rng)
        if not len(ci):
            continue
        loss, diff, length = _path_losses(cells, s, ci, si, freq, cfg, source, obstruction, batch_samples, progress)
        pairs = pd.DataFrame({"cell": ci, "site_id": s.site_id.to_numpy()[si], "loss": loss, "diff": diff,
                              "km": length / 1000,
                              "bearing": bearing_deg(s.lat.to_numpy()[si], s.lon.to_numpy()[si],
                                                     cells.lat.to_numpy()[ci], cells.lon.to_numpy()[ci])})
        pairs = pairs.merge(s_ops[["site_id", "operator"]], on="site_id")
        eirp = pairs.operator.map({op: cfg.rs_eirp_dbm(op) for op in group_ops})
        gain = np.array([cfg.antenna_gain_db(b, o) for b, o in zip(pairs.bearing, pairs.operator)], dtype=float) \
            if cfg.antenna_pattern != "omni" else 0.0
        pairs["rsrp"] = eirp + gain + cfg.rx_gain_dbi - pairs.loss
        per_pair.append(pairs)
    return summarise(cells.reset_index(drop=True), pd.concat(per_pair, ignore_index=True) if per_pair else None,
                     cfg, region)


def summarise(cells: pd.DataFrame, pairs: pd.DataFrame | None, cfg: RadioConfig, region: str) -> pd.DataFrame:
    out = cells[["cell_id", "lat", "lon", "environment"]].copy()
    out["best_rsrp_dbm"] = np.nan
    for col in ["best_operator", "best_site_id"]:
        out[col] = None
    for col in ["best_distance_km", "best_path_loss_db", "best_diffraction_db"]:
        out[col] = np.nan
    out["pairs_modelled"] = 0
    if pairs is not None and len(pairs):
        b = pairs.loc[pairs.groupby("cell").rsrp.idxmax().to_numpy()].set_index("cell")
        rows = b.index.to_numpy()
        out.loc[rows, "best_rsrp_dbm"] = b.rsrp.to_numpy()
        out.loc[rows, "best_operator"] = b.operator.to_numpy()
        out.loc[rows, "best_site_id"] = b.site_id.to_numpy()
        out.loc[rows, "best_distance_km"] = b.km.to_numpy()
        out.loc[rows, "best_path_loss_db"] = b.loss.to_numpy()
        out.loc[rows, "best_diffraction_db"] = b["diff"].to_numpy()
        counts = pairs.groupby("cell").size()
        out.loc[counts.index.to_numpy(), "pairs_modelled"] = counts.to_numpy()
        per_op = pairs.loc[pairs.groupby(["cell", "operator"]).rsrp.idxmax().to_numpy()]
    else:
        per_op = pd.DataFrame(columns=["cell", "operator", "rsrp", "site_id", "km", "diff"])
    for op in OPERATORS:
        key = op.lower()
        o = per_op[per_op.operator == op].set_index("cell")
        out[f"rsrp_{key}_dbm"] = np.nan
        out[f"site_{key}"] = None
        out[f"distance_{key}_km"] = np.nan
        out[f"diffraction_{key}_db"] = np.nan
        if len(o):
            rows = o.index.to_numpy()
            out.loc[rows, f"rsrp_{key}_dbm"] = o.rsrp.to_numpy()
            out.loc[rows, f"site_{key}"] = o.site_id.to_numpy()
            out.loc[rows, f"distance_{key}_km"] = o.km.to_numpy()
            out.loc[rows, f"diffraction_{key}_db"] = o["diff"].to_numpy()
    for c in out.columns:
        if c.endswith(("_dbm", "_db", "_km")):
            out[c] = out[c].astype(float).round(1 if not c.endswith("_km") else 2)
    out["rx_height_m"] = cfg.rx_height_m
    out["model_version"] = MODEL_VERSION
    out["region"] = region
    out["built_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return out
