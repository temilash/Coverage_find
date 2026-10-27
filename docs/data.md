# Data

Sources, licences and freshness. Row counts, query times and ages per Zetaris source: [data_report.md](data_report.md); processing steps per source: [data/README.md](../data/README.md); licences in full: [THIRD_PARTY.md](../THIRD_PARTY.md).

## Data sources and licences

Ookla open data (CC BY-NC-SA 4.0), OpenCelliD (CC BY-SA 4.0), OpenStreetMap (ODbL), Ofcom Wireless Telegraphy
Register and Connected Nations, ONS boundaries (OGL), WorldPop (CC BY 4.0), Copernicus DEM GLO-30, and live
postcodes.io, Open-Meteo, Met Office warnings and Environment Agency floods. Because Ookla's licence is
non-commercial, the data and everything derived from it are **for non-commercial use**; the attribution footer
on every page must be kept. Details and pre-existing components: [THIRD_PARTY.md](../THIRD_PARTY.md). Our code: [MIT](../LICENSE).

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

## Repository layout

| Folder | What it holds |
|---|---|
| `coverage_detective/` | the Python package: evidence API, agents (prompts, guard, loop, rules for sample mode), Zetaris and sample-mode clients, planning (zones, scores, siting), FastAPI app |
| `web/` | the three pages (plain HTML/JS, MapLibre + deck.gl) |
| `meterless_service/` | our small Node HTTP service over the Meterless engines (the agents' memory) |
| `vendor/meterless/` | the Meterless H-MEM and World Model reference engines, vendored unchanged (Apache 2.0) |
| `scripts/` | data downloads and builds, Zetaris registration, the data report, sample extracts, recording examples, the Nightshift probe, the Meterless tests, screenshots |
| `examples/` | the 10 recorded runs and 2 summaries replayed by sample mode |
| `input_examples/`, `output_examples/` | `POST /run` requests and the answers they produced |
| `sample_data/` | small committed extracts of every Zetaris source, DEM crops and saved live responses |
| `data/` | the data manifest, reference tables and data notes (built data, runs and memory stores are gitignored) |
| `docs/` | technical reference, data report, submission text, screenshots |
| `tests/` | offline tests (no Zetaris, network or key) |
| `zetaris/` | the Docker Compose overlay that mounts our data into Zetaris |
