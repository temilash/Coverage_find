# Data quality rule: reject Ookla rows with no tests

## The rule

**Reject Ookla rows where `tests = 0`, so a speed figure never carries confidence it has not earned.**

An Ookla row with no tests would still carry a `download_mbps` value. If the diagnosis rules saw "4 Mbps here"
without knowing that nobody measured it, they could score a cause (distance to the mast, capacity strain) on a
number that means nothing. The rule keeps such rows out of the evidence.

## Shown running (DuckDB over the full data, 2 Nov 2026)

Before the filter (10 ms):

```sql
SELECT count(*) AS rows_total FROM CD_OOKLA_MOBILE.ookla_mobile_r7
```

| rows_total |
|---|
| 23,412 |

After the filter (1 ms):

```sql
SELECT count(*) AS rows_kept FROM CD_OOKLA_MOBILE.ookla_mobile_r7 WHERE tests > 0
```

| rows_kept |
|---|
| 23,412 |

The rows the filter drops (0 ms):

```sql
SELECT cell_id, quarter, download_mbps, tests, devices FROM CD_OOKLA_MOBILE.ookla_mobile_r7 WHERE tests = 0 OR tests IS NULL LIMIT 20
```

(no rows)

The same check on the other two Ookla sources:

| Source | Before | After `tests > 0` |
|---|---:|---:|
| `CD_OOKLA_MOBILE_HISTORY.ookla_mobile_history_r7` | 87,725 | 87,725 |
| `CD_OOKLA_MOBILE_TILES.ookla_mobile_tiles` | 420,260 | 420,260 |

**Result: the rule drops no rows.** Ookla's open data only publishes tiles that had tests, and our build
aggregates those tiles, so the smallest `tests` value is 1 (1 ms):

```sql
SELECT min(tests) AS min_tests, sum(CASE WHEN tests < 10 THEN 1 ELSE 0 END) AS rows_under_10_tests, count(*) AS rows_total FROM CD_OOKLA_MOBILE.ookla_mobile_r7
```

| min_tests | rows_under_10_tests | rows_total |
|---|---|---|
| 1 | 15,772 | 23,412 |

## Where the real risk is, and the rules that handle it

A cell with no tests has **no Ookla row at all**, so a join gives it empty (`NULL`) speed and tests, and many
cells have only a handful of tests (12 ms):

```sql
SELECT count(*) AS populated_cells,
       sum(CASE WHEN o.cell_id IS NULL THEN 1 ELSE 0 END) AS no_ookla_row,
       sum(CASE WHEN o.tests BETWEEN 1 AND 9 THEN 1 ELSE 0 END) AS one_to_nine_tests,
       sum(CASE WHEN o.tests >= 30 THEN 1 ELSE 0 END) AS thirty_plus_tests
FROM CD_CELLS.cells_r7 c
LEFT JOIN CD_OOKLA_MOBILE.ookla_mobile_r7 o ON o.cell_id = c.cell_id
WHERE c.population >= 10
```

| populated_cells | no_ookla_row | one_to_nine_tests | thirty_plus_tests |
|---|---|---|---|
| 42,044 | 19,501 | 14,956 | 3,612 |

Of 42,044 populated cells, 19,501 have no
speed test at all and 14,956 have 1-9. So the code enforces these rules:

- **No row means no measurement.** The evidence item for such a cell says "No Ookla speed tests were recorded
  in this cell", is marked weak, and supports `insufficient_data`, never a speed-based cause
  (`coverage_detective/evidence.py`).
- **Every Ookla figure carries its sample size** (tests and devices) into its finding.
- **Under 30 tests is weak** (`MIN_TESTS = 30` in `coverage_detective/diagnosis.py`): a cause resting only on
  weak evidence scores at most 0.5.
- **"Slow" needs 10+ tests** in gap zones (`GAP_MIN_TESTS` in `config.py`), and an improvement opportunity
  with fewer than 10 tests is labelled limited data.

The `tests = 0` rule stays as a cheap guard in case a future Ookla release or a rebuild ever writes such rows.
