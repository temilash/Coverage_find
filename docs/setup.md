# Setup

Running Coverage Detective with the full data: Zetaris, Meterless Nightshift, Docker, the settings and the data build. Sample mode needs none of this: see the [README](../README.md).

## Running it with the full data

Dependencies: Python 3.12, Node 18+, Docker with the **Zetaris** platform bundle (sponsor-provided, default
`~/zetaris-platform`), **Meterless Nightshift** running locally with your NVIDIA key in its Providers tab, and
(only to rebuild the data from scratch) a free OpenCelliD token. The HackOS Participant Setup Guide covers
installing Zetaris and Nightshift.

```bash
cp .env.example .env     # ZETARIS_URL / USER / PASSWORD, METERLESS_RUNTIME_TOKEN (or _FILE), LLM_MODEL, LLM_MODEL_REASONING
scripts/zetaris/compose.sh up -d                       # Zetaris with our read-only data mount
.venv/bin/python scripts/zetaris/register_sources.py   # once the data is built: every CD_* source
.venv/bin/python scripts/nightshift_probe.py           # one request through Nightshift: which model answered
.venv/bin/python run.py                                # http://localhost:8000 (starts Meterless too)
```

The data itself (about 5 GB of downloads, a few hours to build) is described in [docs/technical.md](technical.md).

**Docker, full mode**: the container reaches Zetaris and Nightshift on the host through `host.docker.internal`
(on Linux add `--add-host=host.docker.internal:host-gateway`; Nightshift must listen on an address the
container can reach). It reads your `.env` (mounted read-only; `-e` values win) and needs the built data mounted:

```bash
docker run --rm -p 8000:8000 --add-host=host.docker.internal:host-gateway -v "$PWD/.env:/app/.env:ro" \
  -e SAMPLE_MODE=false -e ZETARIS_URL=http://host.docker.internal:3000 \
  -e METERLESS_BASE_URL=http://host.docker.internal:8088/v1 -v "$PWD/data:/app/data" coverage-detective
```

## Setup with the full data

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env   # fill in Zetaris, Nightshift (token, models), OpenCelliD and (optionally) Ofcom settings
```

Put the two input files in place:

- `data/raw/boundary/uk_countries.geojson`: ONS *Countries Boundaries UK* (all four nations)
- `data/raw/population/population.tif`: WorldPop UK population raster

## Zetaris

All data is queried through [Zetaris](https://www.zetaris.com) running locally in Docker
(the `zetaris-platform` bundle, default `~/zetaris-platform`; start it with its own
README and check with its `./verify.sh`).

1. **Settings** in `.env`: `ZETARIS_URL=http://localhost:3000` (the Zetaris web app; the
   client signs in there and sends SQL through its API proxy), `ZETARIS_USER`,
   `ZETARIS_PASSWORD`.
2. **Mount our data into Zetaris.** Zetaris only reads local files under
   `/home/zetaris/customer-data/shared/` inside its query engine; any other path fails
   with `Invalid file path to access`. `zetaris/docker-compose.coverage.yml` mounts
   `data/processed` there read-only, as `coverage_detective/`. Apply it with the wrapper,
   which passes that file after Zetaris's own `docker-compose.yml` (nothing in the
   zetaris-platform folder is changed):

   ```bash
   scripts/zetaris/compose.sh up -d driver
   ```

   Use `scripts/zetaris/compose.sh` instead of a plain `docker compose` from then on: a
   plain `docker compose up -d` in zetaris-platform recreates the driver without the mount.
   Set `ZETARIS_PLATFORM_DIR` if the bundle is elsewhere.
3. **Register the sources** (after building the data; re-runnable):

   ```bash
   .venv/bin/python scripts/zetaris/register_sources.py
   .venv/bin/python scripts/check_data.py      # cross-source checks through Zetaris
   ```

Each dataset is its own Zetaris source (Lightning database), e.g. `CD_CELLS.cells_r7`,
`CD_OOKLA_MOBILE.ookla_mobile_r7`. Code queries them with
`coverage_detective.data.zetaris.query(sql)`; every query is logged to
`logs/zetaris_queries.jsonl` (time, SQL, duration, rows, error) for the evidence trail.

## Docker

`Dockerfile`: Python 3.12 slim with Node 20's binary, `pip install -r requirements.txt` and `npm ci` for the
Meterless service at build time, `SAMPLE_MODE=true` by default, port 8000 (`run.py --host 0.0.0.0`).
`.dockerignore` keeps `.env`, raw and processed data, `.venv` and `node_modules` out of the image. Full-mode
flags (host.docker.internal for Zetaris and Nightshift, the mounted `.env` and data) are in the README.

## Build the data

```bash
.venv/bin/python scripts/process/build_cells.py   # H3 r7 grid with population and nation
.venv/bin/python scripts/download/ookla.py        # Ookla mobile tiles, UK only, last 4 quarters
.venv/bin/python scripts/process/build_ookla.py   # Ookla speeds per cell + trend
.venv/bin/python scripts/download/opencellid.py   # OpenCelliD UK cells (needs OPENCELLID_TOKEN)
.venv/bin/python scripts/download/osm.py           # OpenStreetMap GB + Ireland extracts (~2.6 GB)
.venv/bin/python scripts/process/build_masts.py   # mast sites + nearest mast / operators per cell
.venv/bin/python scripts/download/ofcom_wtr.py     # Ofcom Wireless Telegraphy Register (~54 MB)
.venv/bin/python scripts/process/build_wtr.py      # register points, merged into the mast sites
.venv/bin/python scripts/download/dem.py           # Copernicus DEM GLO-30 tiles (~1.4 GB)
.venv/bin/python scripts/download/ons_boundaries.py # ONS constituency + local authority boundaries
.venv/bin/python scripts/process/build_ofcom.py    # Ofcom July 2025 area coverage per cell (zip in data/raw/ofcom/)
.venv/bin/python scripts/process/build_terrain.py  # elevation and line of sight per cell
.venv/bin/python scripts/process/build_places.py   # OSM place names (zone names)
.venv/bin/python scripts/process/build_zones.py    # gap zones and improvement opportunities
.venv/bin/python scripts/zetaris/register_sources.py  # every source into Zetaris
.venv/bin/python scripts/check_data.py             # the data report (docs/data_report.md)
.venv/bin/python scripts/build_samples.py          # extracts for sample mode (sample_data/zetaris, dem)
.venv/bin/python scripts/record_examples.py        # re-record the examples (needs LLM_API_KEY)
```

Every script can be re-run safely; pass `--force` to rebuild an existing output.

## Development

```bash
.venv/bin/python -m pytest -q                       # 166 offline tests, including sample mode end to end
scripts/test_meterless.sh                           # the Meterless Engine tests (H-MEM, World Model, service, client)
.venv/bin/python scripts/record_examples.py         # re-record the examples (live model through Nightshift)
.venv/bin/python scripts/save_output_examples.py    # re-save output_examples/ from a running app
.venv/bin/python scripts/build_samples.py           # rebuild the sample extracts from the full data
.venv/bin/python scripts/check_data.py              # the data report through Zetaris
```

**The prompts are frozen.** `examples/index.json` stores a hash of `coverage_detective/agents/prompts/`, and a
test fails if a prompt changes after the examples were recorded. Recorded replies are keyed by example + agent
+ round + call; a missing one fails clearly and never falls back to a live call.
More: [docs/technical.md](technical.md); submission text: [docs/SUBMISSION.md](SUBMISSION.md).
