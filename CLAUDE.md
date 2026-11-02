# Coverage Detective

An open-source, key-free research tool for UK mobile coverage (4G/5G, phones, mobile data; not home broadband).
It explains **why mobile signal is poor** at a point from public data, ranks under-served areas, and (in progress)
estimates coverage with a terrain-aware radio propagation model on a map that zooms from the whole UK to
neighbourhood level. Statistics and fixed rules only: no language model, no API keys.

## Branch rule

`master` holds the hackathon submission and must not change. All work happens on the `extension` branch: commit
there, push with `git push -u origin extension`, never merge, rebase or force-push into `master`, and do not open
a PR into `master`.

## Extension plan (Nov 2026)

1. **Phase 1, done**: sponsor tech and the LLM agent loop removed; DuckDB is the only SQL engine; the diagnosis is
   rule-based (`diagnosis.py`); outreach letters are a fixed template.
2. **Phase 2**: radio propagation model (`coverage_detective/radio/`), terrain only: RSRP per approximate mast
   site to H3 cells; an established model (ITU-R P.1812 if a maintained open implementation exists, else
   free space / Hata plus knife-edge diffraction); all assumptions in one config (band per operator, EIRP,
   antenna height 25 m, receiver height 1.5 m but any height, omni pattern); an optional clutter / building
   height input per point or cell (default none; LiDAR comes later, do not download it); pilot region Surrey
   with the region as a CLI option; Parquet in DuckDB; API; `docs/radio_validation.md` vs Ookla; unit tests;
   the UI labels it a model estimate.
3. **Phase 3**: multi-scale map: H3 roll-ups (res 5 to 9), PMTiles (vector hexagons and sites, raster coverage)
   as static files, zoom-dependent UI with a placeholder building layer, incremental rebuilds,
   `docs/hosting.md` (static hosting, Zenodo archive; never upload anything).
4. **Phase 4**: README, CLAUDE.md, THIRD_PARTY.md and `docs/data_sources.md` rewritten for the new purpose.

After each phase: run the tests, commit, push, summarise, wait for review.

Constraints: openly licensed data and dependencies only (OS OpenData under OGL is fine, no OS premium products);
no API keys (keyless public services may stay; OpenCelliD's free token is an optional build-time input only);
laptop storage, so ask before any download larger than a few GB, with a size and time estimate; ask before
deleting anything not agreed; if code depends on a removed component in a non-obvious way, stop and ask.

## Domain rules

Causes come from a fixed set: distance_to_mast, terrain_blocking, single_operator, capacity_strain, weather,
active_incident, insufficient_data. Each gets a rule score from 0 to 1 (`diagnosis.py`): how strongly the data
points to it, not a probability. Causes are "likely", never confirmed. A cause supported only by weak evidence
scores at most 0.5 (`weak_reason`: labelled weak, Ookla < 30 tests, crowd-sourced sites only, sites with unknown
operators, a region-wide Met Office warning); where 30+ tests measure at or above the UK median no cause scores
above 0.3.

**capacity_strain** (not "peak congestion": Ookla open data has no time-of-day breakdown): speeds are low although
coverage and nearby masts look adequate; evidence is falling speeds over 3-4 quarters and high devices per head.

**single_operator** comes from mast sites, not Ofcom coverage: "only N operators have approximate mast sites
within 5 km", never "only N operators cover this area".

**Confirmed vs inferred operators**: `operators_confirmed` come from Ofcom's Wireless Telegraphy Register
(licensed microwave links at the site): say "confirmed by Ofcom's licence register". `operators_inferred` come
from OpenCelliD network codes / OSM tags: say "inferred from crowd-sourced data". The register lists fixed links,
not base stations, so a site missing from it is not evidence of anything.

**Mast wording**: say "approximate mast sites", never a count of "masts" (one real mast can appear as several
sites). Use `operator_data_status` per cell (`confirmed`, `inferred`, `unknown`, `no_sites_found`). Whenever
`masts_unknown_operator_within_5km` > 0, say "operators could not be identified for N nearby sites". "Masts
found" is stronger evidence than "no masts found": OpenCelliD's free data is incomplete. Never say "no coverage".

**Ofcom**: no address-level data (the postcode API is not available). Connected Nations area data (July 2025,
constituency / local authority) is regional context only: "In <area>, X% of land has no outdoor 4G from any
operator and Y% from only one (Ofcom Connected Nations, July 2025)". Resident actions always include "Check your
address on Ofcom's mobile coverage checker" (https://www.ofcom.org.uk/mobile-coverage-checker).

**Freshness**: every evidence item carries `data_period` and `as_of` (stored tables via
`manifest.evidence_meta(<table>)`, live sources the fetch time). Every build script calls `manifest.record(...)`.

**Principles**: code and SQL compute every number; every claim links to an evidence item; sample mode keeps runs
reproducible and offline; always carry sample sizes (tests, devices) with Ookla figures.

**Limitations** (state them in outputs): causes are inferred, not confirmed; Ookla is quarterly with no
time-of-day detail; no address-level Ofcom coverage, and Ofcom's area figures are predictions; mast data is
crowd-sourced, incomplete and approximate, and operator labels follow the reference table (EE = T-Mobile + Orange;
Vodafone UK + Three UK = VodafoneThree, 2025); terrain "blocked" means weaker, not none; antenna heights mostly
assumed (25 m); GLO-30 is a surface model (some trees and buildings), most reliable for hills and valleys; mobile
only. Model estimates (Phase 2) are not measurements.

## Layout

```
coverage_detective/     package
  config.py             paths, .env loading, H3_RES=7, UK_BBOX, SAMPLE_LOCATIONS, sample_mode(), zone thresholds
  evidence.py           evidence bundle for a point (DuckDB queries Q1-Q4, line of sight, live sources)
  diagnosis.py          rule-based causes, caps, headline, ruled out, actions
  outreach.py           council / operator contacts, the template letter, the composed email
  planning/             zones.py (gap zones), opportunities.py, scoring.py (priority / opportunity score), siting.py
  data/
    db.py               DuckDB views over data/processed/ (or sample_data/db/), query log, sample-area helpers
    grid.py, masts.py, terrain.py (DEM reader + line_of_sight()), queries.py (cell_sql), manifest.py
  sources/              stored: ookla, opencellid, osm_masts, osm_places, ofcom, ofcom_wtr;
                        live: live.py (timeout, retry, cache, sample fallback), geocode, weather,
                        met_office_warnings, flood_warnings
  api/app.py            FastAPI: explorer API, /api/live, /api/los, POST /api/evidence; pages /, /signal, /council
  api/diagnosis.py      POST /api/diagnose, GET /api/outreach/recipients, POST /api/email
  api/council.py        /api/council/* (view, scopes, ranked zones, CSV, zone detail with fixes)
scripts/download/       fetch raw data into data/raw/<source>/ (skip existing files)
scripts/process/        build data/processed/*.parquet (+ build_web_layers.py for the map)
scripts/check_data.py   data report (docs/data_report.md); --phases for detailed checks
scripts/refresh.py      refresh stored sources on their cadence
scripts/build_samples.py sample extracts (sample_data/db/, sample_data/dem/)
scripts/check_outreach.py contact tables + composed emails, offline
tests/                  pytest, offline (samples, mocked DB / DEM)
web/                    pages, no build step (MapLibre 5.24 + deck.gl 9.4 + h3-js 4.5): index.html + app.js
                        (Explore), signal.html + signal.js (My signal), council.html + council.js, style.css
run.py                  start the app on :8000 (--sample for offline mode)
sample_data/            committed extracts (db/), DEM crops (dem/), saved live responses
data/reference/         small reference tables (committed): network codes, operator groups, WTR licensees, contacts
data/manifest.json      data versions (committed); data/raw, data/processed, data/web, data/cache are gitignored
```

## Running

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python run.py --sample                      # offline sample mode
.venv/bin/python run.py                               # full data in data/processed/
.venv/bin/python -m pytest -q
```

Build order and data notes: docs/setup.md and data/README.md. Inputs placed by hand:
`data/raw/boundary/uk_countries.geojson` (ONS countries) and `data/raw/population/population.tif` (WorldPop UK).
The full processed data is on this machine (data/processed/, 15 tables, plus data/raw/dem/ GLO-30 tiles).

## Data facts

- H3 r7 grid: 56,592 cells, 67.41 m people. Ookla 2026Q3 + 3 previous quarters: 23,412 cells with tests,
  420,260 tiles (zoom 16, ~350-400 m). 42,044 populated cells: 19,501 with no tests, 14,956 with 1-9.
- Mast sites: OpenCelliD cells clustered at 150 m + OSM masts merged within 200 m + Ofcom register sites:
  93,638 sites. Register licensees matched by exact name (Telefonica -> O2, Vodafone, MBNL -> EE + Three).
- Terrain: `line_of_sight()` with 30 m samples, k = 4/3, antenna height from the register else 25 m.
  Everything a user sees for a location uses the exact point, never the cell centre.
- Gap zones: 677 (rules in planning/zones.py); improvement opportunities: 1,816.
- Sample places: Great Langdale (54.445, -3.105; every operator's nearest site blocked, 135-291 m), Central
  London (WC2N 5DU; 133.6 Mbps from 1,425 tests), Near Rhayader (52.30, -3.51; only Vodafone within 5 km),
  CO9 1TP (4.6 Mbps from 3 tests), Sutton St Edmund (flat), a remote cell near Bettws (no tests, no site).

## Rules

- Never commit secrets: `.env` is gitignored, `.env.example` has placeholders only.
- Raw downloads go in `data/raw/` (gitignored). Committed sample data stays small (`sample_data/`).
- Record every source's licence and processing steps in data/README.md.
- Scripts are re-runnable: skip downloads that already exist; `--force` rebuilds.
- Simple, readable code. If an external source is blocked or slow, say so with options rather than working
  around it silently.
