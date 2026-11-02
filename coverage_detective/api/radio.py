"""Radio model API: estimated coverage (RSRP) per H3 r7 cell, overall and per operator.

  GET /api/radio/meta                  regions modelled, the model's assumptions, how to read it
  GET /api/radio/cells?bbox=&operator= [cell_id, RSRP] for the modelled cells in a bbox (operator: best or a name)
  GET /api/radio/cell/{cell_id}        one cell: best server and every operator

Every response carries "label": these are model estimates (coverage_detective/radio/), not measurements.
Data: CD_RADIO.radio_coverage_r7 (scripts/radio/build_coverage.py), only for the regions built so far.
"""

from __future__ import annotations

import json

import h3
from fastapi import APIRouter, HTTPException, Query

from coverage_detective.config import H3_RES
from coverage_detective.data import db, manifest
from coverage_detective.data.db import QUERY_LOG, DBError, query
from coverage_detective.radio.config import MODEL_LABEL, OPERATORS, RSRP_CLASSES, rsrp_class

router = APIRouter(prefix="/api/radio")
TABLE = "CD_RADIO.radio_coverage_r7"
NOTE = ("Estimated 4G signal strength (RSRP) at the cell centre, outdoors at 1.5 m, from approximate mast sites "
        "over terrain (Hata / COST-231 Hata + Bullington diffraction, 800 MHz, omni antennas, 25 m unless Ofcom's "
        "register gives a height). Mast data is crowd-sourced and incomplete; no buildings yet. Compare with "
        "Ofcom's mobile coverage checker for an address.")


def _run(sql: str, params: list | None = None) -> list[dict]:
    try:
        return query(sql, params)
    except DBError as e:
        if "radio_coverage_r7" in str(e):
            raise HTTPException(404, "No radio model results yet: run scripts/radio/build_coverage.py --region <name>")
        raise HTTPException(502, f"Query failed: {e}") from e


def _meta() -> dict:
    try:
        return manifest.evidence_meta("radio_coverage_r7")
    except KeyError:
        return {}


@router.get("/meta")
def meta() -> dict:
    rows = _run(f"SELECT region, count(*) AS cells, median(best_rsrp_dbm) AS median_rsrp_dbm, "
                f"min(built_at) AS built_at, any_value(model_version) AS model_version FROM {TABLE} "
                f"GROUP BY region ORDER BY region")
    cfg_path = db.data_dir() / "radio_config.json"   # written by the build (and copied into the samples)
    return {"label": MODEL_LABEL, "note": NOTE, "regions": rows,
            "classes": [{"min_dbm": None if f == float("-inf") else f, "label": n} for f, n in RSRP_CLASSES],
            "config": json.loads(cfg_path.read_text()) if cfg_path.exists() else None, **_meta()}


@router.get("/cells")
def cells(bbox: str = Query(..., description="minLon,minLat,maxLon,maxLat"),
          operator: str = Query("best", description="best, EE, O2, Three or Vodafone")) -> dict:
    try:
        w, s, e, n = (float(v) for v in bbox.split(","))
    except ValueError:
        raise HTTPException(400, "bbox must be minLon,minLat,maxLon,maxLat") from None
    if operator != "best" and operator not in OPERATORS:
        raise HTTPException(400, f"operator must be best or one of {', '.join(OPERATORS)}")
    col = "best_rsrp_dbm" if operator == "best" else f"rsrp_{operator.lower()}_dbm"
    rows = _run(f"SELECT cell_id, {col} AS rsrp_dbm FROM {TABLE} WHERE lon BETWEEN ? AND ? AND lat BETWEEN ? AND ? "
                f"AND {col} IS NOT NULL ORDER BY cell_id", [w, e, s, n])
    return {"label": MODEL_LABEL, "operator": operator, "count": len(rows),
            "cells": [[r["cell_id"], r["rsrp_dbm"]] for r in rows], **_meta()}


@router.get("/cell/{cell_id}")
def cell(cell_id: str) -> dict:
    if not h3.is_valid_cell(cell_id) or h3.get_resolution(cell_id) != H3_RES:
        raise HTTPException(400, f"{cell_id!r} is not an H3 resolution-{H3_RES} cell")
    sql = f"SELECT * FROM {TABLE} WHERE cell_id = ?"
    rows = _run(sql, [cell_id])
    if not rows:
        raise HTTPException(404, f"Cell {cell_id} is not in a modelled region (see /api/radio/meta)")
    r = rows[0]
    operators = [{"operator": op, "rsrp_dbm": r[f"rsrp_{op.lower()}_dbm"], "class": rsrp_class(r[f"rsrp_{op.lower()}_dbm"]),
                  "site_id": r[f"site_{op.lower()}"], "distance_km": r[f"distance_{op.lower()}_km"],
                  "diffraction_db": r[f"diffraction_{op.lower()}_db"]} for op in OPERATORS]
    best = {"operator": r["best_operator"], "rsrp_dbm": r["best_rsrp_dbm"], "class": rsrp_class(r["best_rsrp_dbm"]),
            "site_id": r["best_site_id"], "distance_km": r["best_distance_km"],
            "path_loss_db": r["best_path_loss_db"], "diffraction_db": r["best_diffraction_db"]}
    return {"label": MODEL_LABEL, "note": NOTE, "cell_id": cell_id, "lat": r["lat"], "lon": r["lon"],
            "environment": r["environment"], "rx_height_m": r["rx_height_m"], "region": r["region"],
            "model_version": r["model_version"], "pairs_modelled": r["pairs_modelled"], "best_server": best,
            "operators": operators, "query": {"sql": sql, "duration_ms": QUERY_LOG[-1]["duration_ms"]}, **_meta()}
