# Setup

Installing Coverage Detective, building the data, and running it. Sample mode needs none of the data:
`python run.py --sample` runs offline on the small committed extracts (see [usage.md](usage.md)).

## Install

Python 3.12. No API keys.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python run.py --sample          # offline, on the committed extracts: http://localhost:8000
```

The only setting is optional and used by one download script: `OPENCELLID_TOKEN` (a free OpenCelliD account
token). Copy `.env.example` to `.env` if you want it. Without it, mast sites come from OpenStreetMap and the
Ofcom register only.

## Build the data

About 5 GB of downloads and a few hours of processing for the whole UK. Two inputs are placed by hand:

- `data/raw/boundary/uk_countries.geojson`: ONS *Countries Boundaries UK* (all four nations)
- `data/raw/population/population.tif`: WorldPop UK population raster

```bash
.venv/bin/python scripts/process/build_cells.py    # H3 r7 grid with population and nation
.venv/bin/python scripts/download/ookla.py         # Ookla mobile tiles, UK only, last 4 quarters
.venv/bin/python scripts/process/build_ookla.py    # Ookla speeds per cell + trend
.venv/bin/python scripts/download/opencellid.py    # optional: OpenCelliD UK cells (needs OPENCELLID_TOKEN)
.venv/bin/python scripts/download/osm.py           # OpenStreetMap GB + Ireland extracts (~2.6 GB)
.venv/bin/python scripts/process/build_masts.py    # mast sites + nearest site / operators per cell
.venv/bin/python scripts/download/ofcom_wtr.py     # Ofcom Wireless Telegraphy Register (~54 MB)
.venv/bin/python scripts/process/build_wtr.py      # register points, merged into the mast sites
.venv/bin/python scripts/download/dem.py           # Copernicus DEM GLO-30 tiles (~1.4 GB)
.venv/bin/python scripts/download/ons_boundaries.py # ONS constituency + local authority boundaries
.venv/bin/python scripts/process/build_ofcom.py    # Ofcom July 2025 area coverage per cell (zip in data/raw/ofcom/)
.venv/bin/python scripts/process/build_terrain.py  # elevation and line of sight per cell
.venv/bin/python scripts/process/build_places.py   # OSM place names (zone names)
.venv/bin/python scripts/process/build_zones.py    # gap zones and improvement opportunities
.venv/bin/python scripts/process/build_web_layers.py # map layers for Explore (data/web/layers/)
.venv/bin/python scripts/check_data.py             # the data report (docs/data_report.md)
.venv/bin/python scripts/build_samples.py          # extracts for sample mode (sample_data/db, sample_data/dem)
```

Every script can be re-run safely; existing downloads are skipped and `--force` rebuilds an output. Every build
script records its data period, inputs and outputs in `data/manifest.json`.

## The data layer

DuckDB is the only SQL engine (`coverage_detective/data/db.py`). Each processed Parquet file is a view in its own
schema, e.g. `CD_CELLS.cells_r7` reads `data/processed/cells_r7.parquet`; in sample mode the same views read
`sample_data/db/`. Nothing is loaded or registered ahead of time: a rebuilt file is read on the next app start.
Every query is logged to `logs/queries.jsonl` (time, SQL, duration, rows, error) and shown with the evidence it
produced.

## Run

```bash
.venv/bin/python run.py                  # full data: http://localhost:8000
.venv/bin/python run.py --sample         # offline sample mode
```

## Refresh

```bash
.venv/bin/python scripts/refresh.py --check-only   # what is due
.venv/bin/python scripts/refresh.py                # refresh what is due
```

Cadence: Ookla when a new quarter is published; OpenCelliD weekly; OpenStreetMap and the Ofcom register monthly;
Ofcom Connected Nations each release (download the zip by hand); WorldPop, ONS and the DEM manually.

## Docker

```bash
docker build -t coverage-detective .
docker run --rm -p 8000:8000 coverage-detective                                       # sample mode
docker run --rm -p 8000:8000 -e SAMPLE_MODE=false -v "$PWD/data:/app/data" coverage-detective   # full data
```

## Development

```bash
.venv/bin/python -m pytest -q                       # offline tests, including sample mode end to end
.venv/bin/python scripts/check_outreach.py --to all # the contact tables and the composed emails (offline)
```
