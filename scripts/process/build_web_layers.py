"""Precompute map layers for the data explorer.

    python scripts/process/build_web_layers.py

Writes data/web/layers/<name>.json, a compact array of [cell_id, value] (plus a third
element 1 for weak evidence, e.g. fewer than 10 Ookla tests), and data/web/layers/index.json
describing every layer (description, units, colour scale, data date from the manifest).
Layers whose inputs are not built yet are skipped. data/web/ is generated and gitignored.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from coverage_detective.config import PROCESSED, ROOT  # noqa: E402
from coverage_detective.data import manifest  # noqa: E402

OUT_DIR = ROOT / "data" / "web" / "layers"
WEAK_TESTS = 10

SPEED_BINS = {"type": "bins", "breaks": [2, 5, 10], "colors": ["#C2410C", "#F59E0B", "#93C5FD", "#2563EB"],
              "labels": ["< 2", "2 – 5", "5 – 10", "> 10"]}


def _read(name: str, columns: list[str]) -> pd.DataFrame | None:
    path = PROCESSED / name
    return pd.read_parquet(path, columns=columns) if path.exists() else None


def _rows(ids, values, weak=None, decimals: int | None = 2) -> list:
    out = []
    for i, cid in enumerate(ids):
        v = values[i]
        if v is None or (isinstance(v, float) and np.isnan(v)):
            continue
        if isinstance(v, (float, np.floating)):
            v = round(float(v), decimals) if decimals is not None else float(v)
        elif isinstance(v, (np.integer,)):
            v = int(v)
        row = [cid, v]
        if weak is not None and weak[i]:
            row.append(1)
        out.append(row)
    return out


def layer_defs() -> list[dict]:
    """Every layer: metadata plus a function producing its rows (None if inputs are missing)."""
    cells = _read("cells_r7.parquet", ["cell_id", "population"])
    ookla = _read("ookla_mobile_r7.parquet", ["cell_id", "download_mbps", "tests", "download_change_pct", "quarters_with_data"])
    masts = _read("masts_r7.parquet", ["cell_id", "nearest_mast_km", "distinct_operators_within_5km", "operator_data_status",
                                       "microwave_backhaul_sites_within_5km"])
    ofcom = _read("ofcom_area_r7.parquet", ["cell_id", "g4_geo_out_0", "g4_geo_out_1"])
    los = _read("los_r7.parquet", ["cell_id", "los_class"])
    terr = _read("terrain_r7.parquet", ["cell_id", "elevation_range_m"])
    dep = _read("deprivation_r7.parquet", ["cell_id", "deprivation_decile_within_nation"])
    radio = _read("radio_coverage_r7.parquet", ["cell_id", "best_rsrp_dbm"])

    defs = []

    def add(name, title, description, units, scale, table, frame, column, weak=None, decimals=2, model=False):
        if frame is None:
            return
        defs.append({
            "name": name, "title": title, "description": description, "units": units, "scale": scale,
            "table": table, "model_estimate": model,
            "rows": lambda: _rows(frame.cell_id.to_numpy(), frame[column].to_numpy(),
                                  None if weak is None else weak(frame).to_numpy(), decimals),
        })

    weak_tests = lambda f: f.tests < WEAK_TESTS  # noqa: E731
    add("download_mbps", "Download speed", "Test-weighted mean mobile download speed, latest quarter "
        "(Ookla). Faded cells have fewer than 10 tests.", "Mbps", SPEED_BINS, "ookla_mobile_r7", ookla,
        "download_mbps", weak_tests)
    add("tests", "Speed tests", "Number of Ookla mobile speed tests in the latest quarter.", "tests",
        {"type": "bins", "breaks": [10, 50, 200], "colors": ["#DBEAFE", "#93C5FD", "#3B82F6", "#1E3A8A"],
         "labels": ["< 10", "10 – 49", "50 – 199", "200 +"]}, "ookla_mobile_r7", ookla, "tests", decimals=0)
    add("trend_pct", "Speed trend", "Change in download speed from the oldest to the latest of the last "
        "4 quarters. Faded: fewer than 10 tests or fewer than 3 quarters.", "%",
        {"type": "bins", "breaks": [-25, -5, 5, 25],
         "colors": ["#C2410C", "#FDBA74", "#E5E7EB", "#93C5FD", "#1D4ED8"],
         "labels": ["< -25%", "-25 – -5%", "-5 – 5%", "5 – 25%", "> 25%"]}, "ookla_mobile_r7", ookla,
        "download_change_pct", lambda f: (f.tests < WEAK_TESTS) | (f.quarters_with_data < 3), decimals=1)
    add("population", "Population", "People per cell (WorldPop 2020).", "people",
        {"type": "bins", "breaks": [1, 100, 1000, 10000], "colors": ["#F3F4F6", "#D1FAE5", "#6EE7B7", "#10B981", "#065F46"],
         "labels": ["0", "1 – 99", "100 – 999", "1k – 10k", "10k +"]}, "cells_r7", cells, "population", decimals=0)
    add("nearest_mast_km", "Nearest mast site", "Distance from the cell centre to the nearest approximate "
        "mast site (OpenCelliD + OpenStreetMap).", "km",
        {"type": "bins", "breaks": [1, 2, 5, 10], "colors": ["#1D4ED8", "#93C5FD", "#FDE68A", "#F59E0B", "#C2410C"],
         "labels": ["< 1", "1 – 2", "2 – 5", "5 – 10", "> 10"]}, "masts_r7", masts, "nearest_mast_km")
    add("operators_within_5km", "Operators within 5 km", "Distinct operators (EE, O2, Vodafone, Three) with "
        "approximate mast sites within 5 km. Operators can be unknown for some sites.", "operators",
        {"type": "categories", "values": [0, 1, 2, 3, 4],
         "colors": ["#C2410C", "#F59E0B", "#FDE68A", "#93C5FD", "#1D4ED8"],
         "labels": ["0", "1", "2", "3", "4"]}, "masts_r7", masts, "distinct_operators_within_5km", decimals=0)
    add("operator_data_status", "Operator data", "How the operators of approximate mast sites within 5 km are "
        "known: confirmed by Ofcom's licence register, inferred from OpenCelliD / OpenStreetMap, or unknown.", "",
        {"type": "categories", "values": ["confirmed", "inferred", "unknown", "no_sites_found"],
         "colors": ["#1D4ED8", "#93C5FD", "#F59E0B", "#C2410C"],
         "labels": ["Confirmed (Ofcom register)", "Inferred only", "Operators unknown", "No sites found"]},
        "masts_r7", masts, "operator_data_status")
    add("microwave_backhaul_sites_within_5km", "Microwave backhaul sites", "Approximate mast sites within 5 km "
        "with licensed microwave links of a mobile operator (Ofcom Wireless Telegraphy Register).", "sites",
        {"type": "bins", "breaks": [1, 3, 10, 30], "colors": ["#F3F4F6", "#DDD6FE", "#A78BFA", "#7C3AED", "#4C1D95"],
         "labels": ["0", "1 – 2", "3 – 9", "10 – 29", "30 +"]}, "masts_r7", masts,
        "microwave_backhaul_sites_within_5km", decimals=0)
    add("ofcom_geo_4g_none_pct", "Ofcom: land with no 4G", "% of the constituency's land with no outdoor 4G "
        "from any operator (Ofcom Connected Nations). Area figure, not this exact cell.", "%",
        {"type": "bins", "breaks": [1, 5, 10, 25], "colors": ["#EFF6FF", "#FDE68A", "#F59E0B", "#EA580C", "#9A3412"],
         "labels": ["< 1%", "1 – 5%", "5 – 10%", "10 – 25%", "> 25%"]}, "ofcom_area_r7", ofcom, "g4_geo_out_0", decimals=1)
    add("ofcom_geo_4g_one_pct", "Ofcom: land with one 4G operator", "% of the constituency's land with "
        "outdoor 4G from only one operator (Ofcom Connected Nations). Area figure.", "%",
        {"type": "bins", "breaks": [1, 5, 10, 25], "colors": ["#EFF6FF", "#FDE68A", "#F59E0B", "#EA580C", "#9A3412"],
         "labels": ["< 1%", "1 – 5%", "5 – 10%", "10 – 25%", "> 25%"]}, "ofcom_area_r7", ofcom, "g4_geo_out_1", decimals=1)
    add("elevation_range", "Elevation range", "Highest minus lowest ground in the cell (Copernicus GLO-30, "
        "a surface model).", "m",
        {"type": "bins", "breaks": [25, 75, 150, 300], "colors": ["#F5F5F4", "#D6D3D1", "#A8A29E", "#78716C", "#44403C"],
         "labels": ["< 25", "25 – 74", "75 – 149", "150 – 299", "300 +"]}, "terrain_r7", terr, "elevation_range_m",
        decimals=0)
    add("los_blocked", "Line of sight to nearest site", "Does terrain rise above the line from the cell centre "
        "(1.5 m) to its nearest approximate mast site? Antenna height from Ofcom's register, else assumed 25 m. "
        "Marginal = blocked by under 10 m, which may be trees or buildings in the surface model. Blocked means "
        "weaker signal, not none.", "",
        {"type": "categories", "values": ["clear", "marginal", "blocked"], "colors": ["#1D4ED8", "#FDBA74", "#C2410C"],
         "labels": ["Clear", "Marginal (< 10 m)", "Blocked (10 m +)"]}, "los_r7", los, "los_class")
    add("deprivation_decile", "Deprivation decile", "Deprivation decile within the cell's nation "
        "(1 = most deprived). Not comparable across nations.", "decile",
        {"type": "bins", "breaks": [3, 5, 7, 9], "colors": ["#9A3412", "#F59E0B", "#E5E7EB", "#93C5FD", "#1D4ED8"],
         "labels": ["1 – 2", "3 – 4", "5 – 6", "7 – 8", "9 – 10"]}, "deprivation_r7", dep,
        "deprivation_decile_within_nation", decimals=0)
    add("rsrp_best_dbm", "Model estimate: best signal", "MODEL ESTIMATE, NOT A MEASUREMENT. Estimated 4G "
        "signal strength (RSRP) at the cell centre from the strongest approximate mast site of any operator: "
        "Hata / COST-231 Hata path loss plus terrain diffraction, 800 MHz, omni antennas, outdoors at 1.5 m. Only "
        "the regions modelled so far (scripts/radio/build_coverage.py). Mast data is crowd-sourced and "
        "incomplete; no buildings yet.", "dBm (RSRP)",
        {"type": "bins", "breaks": [-110, -100, -90, -80],
         "colors": ["#C2410C", "#F59E0B", "#FDE68A", "#93C5FD", "#1D4ED8"],
         "labels": ["< -110 very weak", "-110 – -100 weak", "-100 – -90 fair", "-90 – -80 good", "-80 + strong"]},
        "radio_coverage_r7", radio, "best_rsrp_dbm", decimals=0, model=True)
    return defs


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    index = []
    for d in layer_defs():
        rows = d.pop("rows")()
        (OUT_DIR / f"{d['name']}.json").write_text(json.dumps(rows, separators=(",", ":")))
        try:
            meta = manifest.evidence_meta(d["table"])
            d["data_period"], d["as_of"] = meta["data_period"], meta["as_of"]
        except KeyError:
            d["data_period"], d["as_of"] = None, None
        d["cells"] = len(rows)
        index.append(d)
        size = (OUT_DIR / f"{d['name']}.json").stat().st_size
        print(f"  {d['name']:<24} {len(rows):>6,} cells  {size / 1e6:5.2f} MB")
    (OUT_DIR / "index.json").write_text(json.dumps(index, indent=1))
    print(f"wrote {len(index)} layers to {OUT_DIR}")


if __name__ == "__main__":
    main()
