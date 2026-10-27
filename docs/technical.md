# Technical reference

Setup details, the data pipeline and each page's behaviour. The project overview is in the [README](../README.md); data licences in [THIRD_PARTY.md](../THIRD_PARTY.md).

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

## Pre-existing components

Not written for this project, used unchanged:

| Component | From | Licence | Used for |
|---|---|---|---|
| Meterless H-MEM reference engine (`vendor/meterless/hmem/`) | [Meterless/Meterless](https://github.com/Meterless/Meterless) `engines/hmem/reference` | Apache 2.0 | agents' shared memory: evidence, beliefs, critiques, trust ledger (audit trail) |
| Meterless World Model reference engine (`vendor/meterless/world-model/`) | same repo, `engines/world-model/reference` | Apache 2.0 | places, zones, sites, operators, warnings, relationships, events |
| Zetaris Lightning (local Docker stack) | Zetaris | sponsor licence | federated SQL over every dataset |
| Meterless Nightshift (local app, not in this repository) | Meterless | sponsor terms | the AI provider: routes the agents' model calls to NVIDIA Nemotron, with receipts |
| NVIDIA Nemotron 3.5 Super and 3 Ultra (API, not in this repository) | NVIDIA | NVIDIA API terms | the agents' models (through Nightshift) |
| Open-source Python and Node libraries (`requirements.txt`, `meterless_service/package.json`) | PyPI / npm | their own licences ([THIRD_PARTY.md](../THIRD_PARTY.md)) | data processing, API, map, service |
| Map and UI libraries: MapLibre GL, deck.gl, h3-js, OpenFreeMap tiles | npm / CDN | BSD-3 / MIT / Apache 2.0 / ODbL | the web pages |

The vendored engines' own tests run with the rest in `scripts/test_meterless.sh` (below). The upstream commit
is recorded in `vendor/meterless/README.md`. Public datasets were all downloaded during the hackathon (23-25 Oct
2026, dates in `data/manifest.json`); the project's first commit is 25 Oct 2026.

## Meterless service (agent memory)

`meterless_service/server.ts` (Node 18+, `cd meterless_service && npm install`) is a small
HTTP service on port 8100 over the two vendored engines; `python run.py` starts it next to
the web app and waits for its health check. It persists to `data/meterless/` (gitignored).

- H-MEM: runs, evidence items (stable ids per run, idempotent), beliefs (cause + confidence),
  belief revisions as trust-ledger updates (who, old → new confidence, reason), critiques,
  notes (guard interventions, tool calls), the ordered audit trail of a run, and past
  diagnoses near a point (within ~10 km).
- World Model: entities (place, zone, site, operator, warning), relationships (scoped by an
  operator context, e.g. place → nearest_site{EE} → site), events (diagnosis_completed,
  fix_plan_proposed).
- Python client: `coverage_detective/memory/meterless.py` (5 s timeout, one retry). If the
  service is down it logs loudly and uses an in-memory store with the same interface.

Reads are recorded too: the client notes which agent read what from the case file (set per agent by the
coordinator), for the run's trace.

Tests, all in one command:

```bash
scripts/test_meterless.sh   # H-MEM engine 10/10, World Model engine 10/10, the service 4/4, the Python client 2/2
```

## Model provider: Meterless Nightshift

`coverage_detective/agents/llm.py`. Every model call goes to Nightshift first:

- `POST {METERLESS_BASE_URL}/chat/completions` (default `http://127.0.0.1:8088/v1`), OpenAI-compatible.
- Headers: `Authorization: Bearer <token>` (from `METERLESS_RUNTIME_TOKEN`, else the file named by
  `METERLESS_RUNTIME_TOKEN_FILE`, Nightshift's `<state dir>/proxy-token`; read at call time, never stored,
  logged or printed), `meterless-app: coverage-detective`, and `meterless-scope: coverage-detective-<run_id>`
  (`METERLESS_FRESH_SCOPE=1`: a new scope per call). Timeout 180 s (a model load can take minutes).
- Models pinned: `LLM_MODEL` (Nemotron 3.5 Super: investigator, diagnostician, advisor, letters, fix planner) and
  `LLM_MODEL_REASONING` (Nemotron 3 Ultra, thinking on: the critic). `meterless/auto` and ids without a `/`
  are refused.
- From the answer's top-level `meterless` object: `routed_model` (the answering model; else the response's
  `model`), `receipt_id`, `winning_route.provider/kind`, `usage.latency_ms`, and `receipt_envelope`, whose
  Ed25519 signature over the body (compact JSON, sorted keys) is checked (`signature_valid`). A missing object
  is recorded as "no Meterless receipt in the answer". Committed traces keep the ids, not the envelope.
- `STRICT_MODEL=1` rejects an answer whose routed model is not Nemotron.
- Nightshift unreachable, 401 ("unauthorized: check METERLESS_RUNTIME_TOKEN") or 5xx after the retries: NVIDIA
  direct answers (`LLM_API_KEY`, `LLM_BASE_URL`), recorded as a `provider_fallback` note with the reason. A
  4xx such as an unregistered app name is not retried elsewhere ("is the meterless-app name registered in
  Nightshift?").

Each `llm_call` note carries provider, model_requested, model_answered and the receipt; the trace, `/run`'s
`models` and the UI's "Technical details" show them. These details were implemented from two working
Nightshift 1.2.3 integrations; check them against your install with
`.venv/bin/python scripts/nightshift_probe.py --schema` (one plain request, then the two structured-output
styles the agents use: strict `json_schema`, and the schema in the prompt).

## POST /run, traces and sample mode

- `POST /run` (`coverage_detective/api/agents.py`): `{"lat", "lon"}`, `{"place"}` or `{"zone"}`, plus optional
  `audience` and `timeout_s` (default 600). It starts the run through the same code as `POST /api/diagnose` /
  `POST /api/council/zones/{id}/diagnose`, polls the run store, and returns the result as JSON; a 202 with the
  poll URL if the run is still going at the timeout; a JSON error otherwise.
- Traces (`coverage_detective/agents/trace_log.py`): `logs/run_<run_id>.jsonl`, also on stdout, built at the
  end of each run from the Meterless audit, notes, critiques, tool calls' Zetaris queries, World Model writes
  and recorded reads (fields listed in the README).
- Sample mode (`python run.py --sample` or `SAMPLE_MODE=true`): DuckDB over `sample_data/zetaris/` in place of
  Zetaris, saved live responses, and for the model: recorded replies within 100 m of an example
  (`agents/replay.py`, run at the recorded point), fixed rules elsewhere inside the extracts (`agents/rules.py`),
  and a refusal listing the covered areas outside them (`data/sample_db.covers`).

## Docker

`Dockerfile`: Python 3.12 slim with Node 20's binary, `pip install -r requirements.txt` and `npm ci` for the
Meterless service at build time, `SAMPLE_MODE=true` by default, port 8000 (`run.py --host 0.0.0.0`).
`.dockerignore` keeps `.env`, raw and processed data, `.venv` and `node_modules` out of the image. Full-mode
flags (host.docker.internal for Zetaris and Nightshift, the mounted `.env` and data) are in the README.

## Data sources

| Source | What it tells us | Type | Licence | Date / version |
|---|---|---|---|---|
| WorldPop + ONS boundaries | People per cell; nation | file | CC BY 4.0; OGL v3.0 | 2020; ONS Dec 2023 |
| Ookla open data (mobile) | Measured mobile download/upload/latency, tests, devices, trend | file | CC BY-NC-SA 4.0 | 2026Q3 + 3 previous quarters |
| OpenCelliD | Crowd-sourced radio cells → approximate mast sites, operators *inferred* from network codes | file | CC BY-SA 4.0 | downloaded 25 Oct 2026 |
| OpenStreetMap (Geofabrik) | Mapped communication masts and towers | file | ODbL 1.0 | downloaded 25 Oct 2026 |
| Ofcom Wireless Telegraphy Register | Operators' licensed microwave links: operators *confirmed* at sites, antenna heights | file | Ofcom copyright, free re-use | downloaded 25 Oct 2026 |
| Ofcom Connected Nations | Predicted coverage by number of operators, per constituency / local authority; per operator by nation | file | OGL | July 2025 |
| ONS constituencies, local authorities | Which area a cell is in | file | OGL v3.0 | July 2024; Dec 2024 |
| OpenStreetMap places | Names for gap zones (city, town, village, hamlet) | file (from the OSM extracts) | ODbL 1.0 | downloaded 25 Oct 2026 |
| Copernicus DEM GLO-30 | Elevation per cell; line of sight to mast sites | file (93 tiles) | Copernicus DEM licence (free, attribution) | 2021 release (data 2011–2015) |
| postcodes.io | Postcode / place → location, district, region; nearest postcode to a point | REST, live (Python client) | OGL v3 data (ONS, OS, Royal Mail) | at question time |
| Open-Meteo forecast | Weather now and next 24 h (rain, snow, wind, gusts) → severity | REST, live (Python client) | CC BY 4.0, non-commercial, attribution | at question time |
| Met Office severe weather warnings (RSS) | Yellow / amber / red warnings for the point's region | RSS, live (Python client) | Crown copyright, OGL v3 | at question time |
| Environment Agency flood monitoring | Flood alerts / warnings within 10 km (England only) | REST, live: Python client + Zetaris REST source `CD_LIVE_EA_FLOODS` | OGL v3 | at question time |

Details, processing steps and exact licences: `data/README.md`.

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

## Data explorer

A map of every layer.

```bash
.venv/bin/python scripts/process/build_web_layers.py   # precompute map layers -> data/web/layers/
scripts/zetaris/compose.sh up -d                        # Zetaris with our data mount (cell details use it)
.venv/bin/python run.py                                 # http://localhost:8000
```

- Hexagons (H3 r7) coloured by the chosen layer: download speed (< 2, 2–5, 5–10, > 10
  Mbps), tests, speed trend, population, nearest mast site, operators within 5 km,
  operator data status, Ofcom land with no / one 4G operator, elevation range, line of
  sight to the nearest site (clear / marginal / blocked). Cells with fewer than 10
  tests are faded (weak evidence); grey means no data.
- Approximate mast sites appear from zoom 10 (coloured by source), Ookla tiles from zoom 12.
- Click a cell: its details come from **one cross-source Zetaris query** (cells, Ookla and
  history, neighbours, masts, Ofcom area), shown under "Zetaris query used", with data dates.
- **Explore is area level**: the cell panel shows cell aggregates only (speeds and tests,
  neighbours, trend, nearest site and operators within 5 km of the cell, the line-of-sight
  grade from the cell centre, elevation range, Ofcom area figures) and "Data details" with
  the Zetaris SQL. "Check an exact location here" opens My signal at the clicked point.
- Search a postcode or place (postcodes.io). Works on phones (the panel slides up).

API: `GET /api/layers`, `/api/layer/{name}`, `/api/masts?bbox=`, `/api/tiles?bbox=`,
`/api/cell/{cell_id}`, `/api/geocode?q=`, `/api/geocode/reverse?lat=&lon=`,
`/api/live?lat=&lon=` (add `&flood_via=zetaris` for the Zetaris path),
`/api/los?lat=&lon=` (line of sight from the exact point to the nearest site, or
`&site_id=`), `/health`
(OpenAPI docs at `/docs`).

![Explore, Lake District, line-of-sight layer with a cell open](screenshots/explore.png)
Screenshots: `pip install playwright`, then `python scripts/screenshots.py` and
`python scripts/council_screenshots.py` (pass `--chrome <path>` to use an installed Chromium) → `docs/screenshots/`.


## My signal page

`http://localhost:8000/signal` (deep links `?q=LD6 5AD` or `?lat=52.30&lon=-3.51`): point
level, for one exact location. Search a postcode or place, or click the map at the exact
spot. It calls `POST /api/evidence` and shows, with no LLM involved, a map without hexagon
colouring (Ookla tiles within 2 km by speed, identified sites within 10 km by operator and
marked confirmed / inferred / unknown, the point, a faint outline of its hexagon, each
operator's line of sight) and a panel with:

- **Networks compared**: for EE, O2, Three and Vodafone, the nearest identified site
  (distance, direction, source: OpenCelliD / OpenStreetMap / Ofcom register), whether the
  operator is confirmed by Ofcom's licence register or inferred from crowd-sourced data,
  and the line of sight from the exact point (clear / marginal / blocked, obstruction
  height, mast height measured or assumed). Each operator's line is drawn on the map in its
  own colour, red and dashed where blocked, with the highest obstruction marked. "No site
  identified in our data" is never presented as "no coverage".
- **Likely best network here**: a rule (clear lines first, nearest first, confirmed
  operators first on ties; otherwise the least obstructed line), labelled "Based on data,
  not yet reviewed by agents".
- Measured speeds within 1 km (Ookla tiles), in the cell and the neighbouring cells, with
  test counts and strength; the 4-quarter trend; Ofcom area figures (area-level wording);
  current weather, Met Office warnings and flood warnings.
- **Why: the agents' diagnosis** (`POST /api/diagnose`, polled at `GET /api/runs/{id}`): the tabbed run view
  (Result / How the agents decided / Evidence), the evidence letter (`POST /api/runs/{id}/letter`), and a
  `?run=` deep link to reopen a run. "See this area in Explore" goes back to the area view.

![My signal, Great Langdale](screenshots/hero_my_signal.png)

## Council view

`http://localhost:8000/council` (deep links `?scope=la:E06000064&zone=Z00603`): where are
the gaps? Pick an area (UK, a nation or a council), adjust the priority weights, and read a
ranked table of **gap zones** with a map coloured by score. Opening a zone shows its cells,
an evidence summary, a rule-based fix and the top candidate sites with lines to the cells
they would reach. Each zone links to Explore (its cells outlined) and My signal (its
centre). CSV export of the ranking. Two zone types: gap zones (below) and **improvement opportunities**
(cells that are not poorly served but have a measurable weakness: speed well below the area or UK median,
falling speeds, only 1-2 operators identified, or a busy-network signal; reasons computed in code,
`planning/opportunities.py`; 1,816 zones, teal on the map, "limited data" under 10 tests). The list always
matches the map (`GET /api/council/view`; zoom caps in `config.COUNCIL_VIEW`: below zoom 10 the top 40 gap
zones in view, from zoom 10 up to 100 gap zones and 60 opportunities). Any zone can be diagnosed by the
agents (`POST /api/council/zones/{id}/diagnose`); "Priority summary (agents)" summarises what is listed
(`POST /api/council/summary`).

How zones are built (`scripts/process/build_zones.py`, rules in `planning/zones.py`,
thresholds in `config.py`):

- A cell with at least 10 people is **poorly served** if (a) its test-weighted download over
  the cell and neighbour ring is below 5 Mbps from at least 10 tests, or (b) no approximate
  site lies within 5 km, unless at least 10 tests measure at least 5 Mbps (then it is a gap
  in our site data, not in coverage: 227 cells, e.g. Penzance), or (c) it has fewer than 10
  tests and terrain blocks the line (by 10 m or more) to every site within 5 km (the 8
  nearest are checked).
- **Zones** are H3-connected clusters of poorly served cells holding at least 50 people in
  total (a per-cell threshold missed sparse valleys such as Great Langdale). 677 zones,
  264,561 people (25 Oct data). Named after their largest OSM place.
- **Priority score** (`planning/scoring.py`), computed per request: people, severity,
  persistence (share of the last 4 quarters below 5 Mbps), worsening trend and few
  operators, each normalised 0–1 by rank within the chosen area and weighted (defaults
  35 / 25 / 15 / 10 / 15). The API returns each factor's contribution. Data confidence
  (tests) is shown separately. "Need" (deprivation, rural–urban) is left out until built.
- **Fixes** (`planning/siting.py`): a rule picks new site / add operators / capacity
  upgrade / gather measurements; up to 30 DEM high points within 5 km of the zone and the
  existing sites within 10 km are checked by line of sight (25 m mast, 1.5 m receiver) to
  every populated cell within 8 km, ranked by the zone's people gaining a clear line.
  Limits: simplified line of sight, no planning, land, power or backhaul checks.

API: `GET /api/council/scopes`, `/api/council/zones?scope=&weights=people:0.4,...`,
`/api/council/zones.csv`, `/api/council/zones/{zone_id}`, `/api/council/zones/{zone_id}/cells`.

![Council view, Lake District](screenshots/council/03_lake_district_gaps_and_opportunities.png)

## Evidence API

`POST /api/evidence` with `{"query": "WC2N 5DU"}` (postcode or place) or `{"lat": 54.445,
"lon": -3.105}` returns the evidence bundle the agents will work from
(`coverage_detective/evidence.py`):

- the point, its H3 r7 cell and the six neighbours;
- three Zetaris queries, each logged in the bundle (`zetaris_queries`: SQL, time, rows):
  Q1 the cross-source cell query (cells, Ookla with history and neighbours, masts, Ofcom
  area, terrain, line of sight), Q2 approximate mast sites within 10 km of the exact point,
  Q3 UK context (median devices per head and speed; cached per process), Q4 Ookla tiles
  within 1 km of the point (latest quarter);
- line of sight from the exact point to the nearest site of each operator within 10 km,
  and to the nearest site of any operator;
- live weather, Met Office warnings and EA flood warnings.

The bundle also has `operators` (the per-operator comparison used by the My signal page)
and `likely_best` (the rule-based summary, `reviewed_by_agents: false`). Each item has `id`, `source`, `data_period`, `as_of`, `query_or_url`, `cell_ids`,
`finding` (one plain-English sentence built in code, following the wording rules in
CLAUDE.md), `value`, `sample_size`, `strength` (strong / moderate / weak) and `bears_on`
(the causes it is relevant to). The bundle also carries `errors`, `limitations` and the
Ofcom coverage checker link for residents.

Strength, in short: Ookla speed is strong from 50 tests, weak under 10; line of sight is
strong when blocked by 30 m or more, weak when the margin is under 10 m (trees or buildings
in the surface model) or under 1 m of clearance; "no sites found" and operator gaps are weak
(crowd-sourced data is incomplete); unknown-operator sites weaken the operator item unless
all four operators are present; live items are weak when served from a sample.

```bash
curl -s -X POST localhost:8000/api/evidence -H 'content-type: application/json' -d '{"query": "LD6 5AD"}'
```


## Data freshness

Two kinds of data:

- **Live, fetched when a question is asked:** postcode lookup (postcodes.io), weather
  (Open-Meteo), Met Office weather warnings and Environment Agency flood warnings. Their
  evidence is stamped with the fetch time. Clients (`coverage_detective/sources/`) use a
  5 s timeout, one retry and a disk cache (`data/cache/live/`, gitignored; postcodes 30
  days, weather 30 min, warnings and floods 15 min). If a call fails they use the cached
  copy, else a saved sample (`sample_data/`), and say which. `SAMPLE_MODE=true` answers
  from the samples only (offline, reproducible); refresh them with
  `python scripts/download/live_samples.py`.
- **Stored, refreshed periodically** and queried through Zetaris:

  | Source | Refreshed | How |
  |---|---|---|
  | Ookla mobile speeds | quarterly | when a newer quarter appears in the public bucket |
  | OpenCelliD radio cells | weekly | re-download |
  | OpenStreetMap masts | monthly | re-download the Geofabrik extracts |
  | Ofcom Wireless Telegraphy Register | monthly | re-download the CSV |
  | Ofcom Connected Nations | each release (about twice a year) | download the new zip by hand into `data/raw/ofcom/` (no API) |
  | WorldPop population, ONS boundaries, Copernicus DEM | manual | rarely change |

`data/manifest.json` (committed) records for every stored source its data period (e.g.
Ookla `2026Q3`), download dates, build time, refresh cadence and each output's row count
and SHA-256; every build script updates it. Evidence items carry `data_period` and
`as_of` from the manifest (`coverage_detective.data.manifest.evidence_meta`), or the fetch
time for live sources.

```bash
.venv/bin/python scripts/refresh.py --check-only          # what is due
.venv/bin/python scripts/refresh.py                       # refresh what is due
.venv/bin/python scripts/refresh.py --source ookla --force
```

A refresh downloads the new data (keeping the old file until the new one is in), rebuilds
the affected tables, re-registers them in Zetaris and updates the manifest.
