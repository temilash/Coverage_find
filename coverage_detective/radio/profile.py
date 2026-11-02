"""Terrain path profiles between mast sites and receivers, many at once.

A batch of paths is stored "ragged": the interior samples of every path (the two ends stand on their own
ground) in flat arrays, with `seg` giving each sample's path and `starts` the first sample of each path.
Samples are spaced about `spacing_m` apart (30 m = the DEM's resolution) along the straight line in
latitude / longitude, which is close enough to the great circle under ~50 km.

The optional obstruction input raises the profile above the bare DEM, for buildings or trees:
  - None (default): terrain only;
  - an array with one height (m) per interior sample of the batch;
  - any object with heights(lats, lons) -> metres, e.g. CellClutter built from a per-cell summary
    (median building height and built fraction, such as the LiDAR building heights to come).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import h3
import numpy as np
import pandas as pd

from coverage_detective.data.terrain import distance_m


@dataclass
class Profiles:
    length_m: np.ndarray      # per path
    ground_tx: np.ndarray     # ground height at the site end, m
    ground_rx: np.ndarray     # ground height at the receiver end, m
    seg: np.ndarray           # per interior sample: its path
    starts: np.ndarray        # first interior sample of each path
    di: np.ndarray            # per interior sample: distance from the site, m
    hi: np.ndarray            # per interior sample: ground + obstruction, m
    obstruction: np.ndarray   # per interior sample: the obstruction part of hi, m

    def __len__(self) -> int:
        return len(self.length_m)


def sample_counts(length_m, spacing_m: float) -> np.ndarray:
    """Interior samples per path: at least one, so every path has a profile."""
    return np.maximum(np.ceil(np.asarray(length_m) / spacing_m).astype(np.int64) - 1, 1)


def build(tx_lat, tx_lon, rx_lat, rx_lon, source, spacing_m: float = 30.0, obstruction=None) -> Profiles:
    """Profiles from each site (tx) to its receiver (rx). `source` has elevation(lats, lons) (terrain.DEM)."""
    tx_lat, tx_lon = np.asarray(tx_lat, dtype=float), np.asarray(tx_lon, dtype=float)
    rx_lat, rx_lon = np.asarray(rx_lat, dtype=float), np.asarray(rx_lon, dtype=float)
    length = np.maximum(distance_m(tx_lat, tx_lon, rx_lat, rx_lon), 1.0)
    inner = sample_counts(length, spacing_m)
    starts = np.concatenate([[0], np.cumsum(inner)[:-1]])
    seg = np.repeat(np.arange(len(length)), inner)
    f = (np.arange(len(seg)) - starts[seg] + 1) / (inner[seg] + 1)
    lats = tx_lat[seg] + (rx_lat - tx_lat)[seg] * f
    lons = tx_lon[seg] + (rx_lon - tx_lon)[seg] * f
    ground = source.elevation(lats, lons).astype(float)
    ends = source.elevation(np.concatenate([tx_lat, rx_lat]), np.concatenate([tx_lon, rx_lon])).astype(float)
    if obstruction is None:
        extra = np.zeros(len(seg))
    elif hasattr(obstruction, "heights"):
        extra = np.asarray(obstruction.heights(lats, lons), dtype=float)
    else:
        extra = np.asarray(obstruction, dtype=float)
        if extra.shape != ground.shape:
            raise ValueError(f"obstruction has {extra.shape} heights; the batch has {ground.shape} interior samples")
    return Profiles(length, ends[:len(length)], ends[len(length):], seg, starts, f * length[seg],
                    ground + extra, extra)


class CellClutter:
    """Obstruction heights from a per-cell summary: each H3 cell's median building height times its built
    fraction (the expected height of what a sample point in the cell hits), on a regular grid for fast lookup.

    table: cell_id, median_height_m, building_fraction (0-1). Points outside every listed cell get 0.
    """

    def __init__(self, table: pd.DataFrame, step_deg: float = 0.001):
        res = h3.get_resolution(table.cell_id.iloc[0])
        effective = dict(zip(table.cell_id, table.median_height_m.fillna(0) * table.building_fraction.fillna(0)))
        bounds = np.array([b for c in table.cell_id for b in h3.cell_to_boundary(c)])
        self.lat0, self.lon0 = bounds[:, 0].min(), bounds[:, 1].min()
        self.step_lat, self.step_lon = step_deg, step_deg / math.cos(math.radians(bounds[:, 0].mean()))
        rows = int((bounds[:, 0].max() - self.lat0) / self.step_lat) + 1
        cols = int((bounds[:, 1].max() - self.lon0) / self.step_lon) + 1
        self.grid = np.zeros((rows, cols), dtype=np.float32)
        for r in range(rows):
            lat = self.lat0 + (r + 0.5) * self.step_lat
            for c in range(cols):
                cid = h3.latlng_to_cell(lat, self.lon0 + (c + 0.5) * self.step_lon, res)
                self.grid[r, c] = effective.get(cid, 0.0)

    def heights(self, lats, lons) -> np.ndarray:
        r = np.floor((np.asarray(lats) - self.lat0) / self.step_lat).astype(np.int64)
        c = np.floor((np.asarray(lons) - self.lon0) / self.step_lon).astype(np.int64)
        inside = (r >= 0) & (r < self.grid.shape[0]) & (c >= 0) & (c < self.grid.shape[1])
        out = np.zeros(r.shape, dtype=float)
        out[inside] = self.grid[r[inside], c[inside]]
        return out
