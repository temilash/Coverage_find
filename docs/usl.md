# Unified semantic layer: `view_point_evidence`

## Use case

The agents diagnose poor mobile signal at a place, and each answer needs the same facts about the place's H3 cell
from five separately registered Zetaris sources: who lives there (`CD_CELLS`), what speed tests measured
(`CD_OOKLA_MOBILE`), which approximate mast sites are near and which operators they belong to (`CD_MASTS`), the
terrain (`CD_TERRAIN`), and whether the line from the cell to its nearest site is blocked (`CD_LOS`).
`view_point_evidence` is that federation written once: one row per cell, the columns the investigator's tools
and the explorer's cell panel read. The production query that does this per place is `cell_sql()` in
`coverage_detective/data/queries.py` (it adds neighbour rings, Ofcom figures and the UK medians); this view is
its core, over the real column names.

## DDL

```sql
CREATE OR REPLACE VIEW view_point_evidence AS
SELECT
  c.cell_id, c.lat, c.lon, c.population, c.nation,
  o.quarter, o.download_mbps, o.upload_mbps, o.latency_ms, o.tests, o.devices,
  m.nearest_mast_id, m.nearest_mast_km, m.nearest_mast_operators, m.masts_within_5km,
  m.distinct_operators_within_5km, m.operator_data_status,
  t.elevation_mean_m, t.elevation_range_m,
  l.site_id AS los_site_id, l.los_class, l.blocked, l.obstruction_m, l.mast_height_source
FROM CD_CELLS.cells_r7 c
LEFT JOIN CD_OOKLA_MOBILE.ookla_mobile_r7 o ON o.cell_id = c.cell_id
LEFT JOIN CD_MASTS.masts_r7 m ON m.cell_id = c.cell_id
LEFT JOIN CD_TERRAIN.terrain_r7 t ON t.cell_id = c.cell_id
LEFT JOIN CD_LOS.los_r7 l ON l.cell_id = c.cell_id
```

**What happened when we ran it (27 Oct 2026, local Zetaris stack).** The view body runs through Zetaris as written
(below). The `CREATE VIEW` statement itself does not stay usable on our local build: inside a Lightning database
(`CD_USL.view_point_evidence`) it fails with `java.util.NoSuchElementException: key not found: cd_usl`, and in the
default catalog the SQL editor accepts it but a later `SELECT` from the view (through either the query API or the
SQL editor) answers `Not exist`. We removed those test objects. So the queries below use the view definition as a
`WITH view_point_evidence AS (...)` clause: the same SQL, federated by Zetaris across the five sources in one
query, and the form our code uses.

## Active queries (run through Zetaris, 27 Oct 2026 15:45 UTC)

**1. One cell: speed, nearest mast and line-of-sight status** (Great Langdale's cell, `871955454ffffff`;
450 ms):

```sql
WITH view_point_evidence AS (
SELECT
  c.cell_id, c.lat, c.lon, c.population, c.nation,
  o.quarter, o.download_mbps, o.upload_mbps, o.latency_ms, o.tests, o.devices,
  m.nearest_mast_id, m.nearest_mast_km, m.nearest_mast_operators, m.masts_within_5km,
  m.distinct_operators_within_5km, m.operator_data_status,
  t.elevation_mean_m, t.elevation_range_m,
  l.site_id AS los_site_id, l.los_class, l.blocked, l.obstruction_m, l.mast_height_source
FROM CD_CELLS.cells_r7 c
LEFT JOIN CD_OOKLA_MOBILE.ookla_mobile_r7 o ON o.cell_id = c.cell_id
LEFT JOIN CD_MASTS.masts_r7 m ON m.cell_id = c.cell_id
LEFT JOIN CD_TERRAIN.terrain_r7 t ON t.cell_id = c.cell_id
LEFT JOIN CD_LOS.los_r7 l ON l.cell_id = c.cell_id
)
SELECT cell_id, population, download_mbps, tests, nearest_mast_id, nearest_mast_km, nearest_mast_operators,
       operator_data_status, elevation_mean_m, los_class, obstruction_m, mast_height_source
FROM view_point_evidence
WHERE cell_id = '871955454ffffff'
```

| cell_id | population | download_mbps | tests | nearest_mast_id | nearest_mast_km | nearest_mast_operators | operator_data_status | elevation_mean_m | los_class | obstruction_m | mast_height_source |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 871955454ffffff | 47.8 |  |  | S075142 | 3.7 | Vodafone | inferred | 247.3 | blocked | 200.8 | assumed_default |

No speed test was recorded in this cell (`tests` is empty), the nearest approximate site is 3.7 km away with
Vodafone inferred from crowd-sourced data, and the line to it is blocked: terrain rises 200.8 m above it (antenna
height assumed).

**2. Every populated cell (10+ people) by line-of-sight class** (612 ms):

```sql
WITH view_point_evidence AS (
SELECT
  c.cell_id, c.lat, c.lon, c.population, c.nation,
  o.quarter, o.download_mbps, o.upload_mbps, o.latency_ms, o.tests, o.devices,
  m.nearest_mast_id, m.nearest_mast_km, m.nearest_mast_operators, m.masts_within_5km,
  m.distinct_operators_within_5km, m.operator_data_status,
  t.elevation_mean_m, t.elevation_range_m,
  l.site_id AS los_site_id, l.los_class, l.blocked, l.obstruction_m, l.mast_height_source
FROM CD_CELLS.cells_r7 c
LEFT JOIN CD_OOKLA_MOBILE.ookla_mobile_r7 o ON o.cell_id = c.cell_id
LEFT JOIN CD_MASTS.masts_r7 m ON m.cell_id = c.cell_id
LEFT JOIN CD_TERRAIN.terrain_r7 t ON t.cell_id = c.cell_id
LEFT JOIN CD_LOS.los_r7 l ON l.cell_id = c.cell_id
)
SELECT los_class, count(*) AS cells, round(sum(population)) AS people,
       round(avg(download_mbps), 1) AS avg_download_mbps, sum(tests) AS tests
FROM view_point_evidence
WHERE population >= 10
GROUP BY los_class
ORDER BY cells DESC
```

| los_class | cells | people | avg_download_mbps | tests |
|---|---|---|---|---|
| clear | 25,724 | 57,572,358 | 87.9 | 360,234 |
| marginal | 8,450 | 7,196,452 | 65.7 | 40,519 |
| blocked | 7,870 | 2,605,005 | 54.0 | 17,165 |

The three groups add up to all 42,044 populated cells. The averages are means of cell averages (not
test-weighted) and cover only the cells that have tests.
