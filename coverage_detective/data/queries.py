"""SQL used by more than one caller (the explorer API and the evidence bundle); DuckDB dialect."""

from __future__ import annotations

import h3

from coverage_detective.config import PROCESSED, sample_mode
from coverage_detective.data import manifest


def _optional_joins() -> tuple[list[str], list[str], list[str]]:
    """Extra sources (built in later phases) joined only when registered in the manifest."""
    sources = manifest.load()["sources"]
    cols, joins, tables = [], [], []
    if "terrain" in sources:
        cols += ["t.elevation_mean_m", "t.elevation_range_m"]
        joins.append("LEFT JOIN CD_TERRAIN.terrain_r7 t ON t.cell_id = c.cell_id")
        tables.append("terrain_r7")
    if "terrain" in sources and ((PROCESSED / "los_r7.parquet").exists() or sample_mode()):
        cols += ["l.blocked AS los_blocked", "l.obstruction_m AS los_obstruction_m", "l.los_class"]
        joins.append("LEFT JOIN CD_LOS.los_r7 l ON l.cell_id = c.cell_id")
        tables.append("los_r7")
    if "deprivation" in sources:
        cols += ["d.rural_urban_class", "d.deprivation_decile_within_nation"]
        joins.append("LEFT JOIN CD_DEPRIVATION.deprivation_r7 d ON d.cell_id = c.cell_id")
        tables.append("deprivation_r7")
    return cols, joins, tables


def cell_sql(cell_id: str) -> tuple[str, list[str]]:
    neighbours = ", ".join(f"'{n}'" for n in sorted(h3.grid_ring(cell_id, 1)))
    extra_cols, extra_joins, extra_tables = _optional_joins()
    extra_select = "".join(f",\n  {c}" for c in extra_cols)
    sql = f"""SELECT
  c.cell_id, c.lat, c.lon, c.population, c.nation,
  o.quarter, o.download_mbps, o.upload_mbps, o.latency_ms, o.tests, o.devices,
  o.tests_per_1k_pop, o.devices_per_1k_pop, o.quarters_with_data,
  o.download_mbps_oldest, o.download_change_pct, o.download_trend_mbps_per_quarter,
  hst.history_json,
  nb.neighbour_download_mbps, nb.neighbour_tests, nb.neighbour_cells_with_data,
  m.nearest_mast_km, m.nearest_mast_bearing_deg, m.nearest_mast_id, m.nearest_mast_lat,
  m.nearest_mast_lon, m.nearest_mast_sources, m.nearest_mast_operators,
  m.nearest_mast_operators_confirmed, m.nearest_mast_antenna_height_m, m.masts_within_5km,
  m.masts_known_operator_within_5km, m.masts_unknown_operator_within_5km,
  m.masts_osm_only_within_5km, m.distinct_operators_within_5km, m.operators_within_5km,
  m.operators_confirmed_within_5km, m.wtr_sites_within_5km, m.microwave_backhaul_sites_within_5km,
  m.operator_data_status,
  a.pcon_code, a.pcon_name, a.laua_code, a.laua_name, a.g4_geo_out_0, a.g4_geo_out_1,
  a.g4_prem_out_0, a.g4_prem_in_0, a.g4_abrd_in_0, a.g5hc_prem_out_at_least_one,
  a.laua_g4_geo_out_0, a.laua_g4_geo_out_1, a.data_date AS ofcom_data_date{extra_select}
FROM CD_CELLS.cells_r7 c
LEFT JOIN CD_OOKLA_MOBILE.ookla_mobile_r7 o ON o.cell_id = c.cell_id
LEFT JOIN (
  SELECT cell_id, to_json(list({{'quarter': quarter, 'download_mbps': download_mbps, 'tests': tests}}))::VARCHAR
         AS history_json
  FROM CD_OOKLA_MOBILE_HISTORY.ookla_mobile_history_r7 WHERE cell_id = '{cell_id}' GROUP BY cell_id
) hst ON hst.cell_id = c.cell_id
CROSS JOIN (
  SELECT sum(download_mbps * tests) / sum(tests) AS neighbour_download_mbps,
         sum(tests) AS neighbour_tests, count(*) AS neighbour_cells_with_data
  FROM CD_OOKLA_MOBILE.ookla_mobile_r7 WHERE cell_id IN ({neighbours})
) nb
LEFT JOIN CD_MASTS.masts_r7 m ON m.cell_id = c.cell_id
LEFT JOIN CD_OFCOM_AREA.ofcom_area_r7 a ON a.cell_id = c.cell_id{"".join(chr(10) + j for j in extra_joins)}
WHERE c.cell_id = '{cell_id}'"""
    tables = ["cells_r7", "ookla_mobile_r7", "ookla_mobile_history_r7", "masts_r7", "ofcom_area_r7", *extra_tables]
    return sql, tables
