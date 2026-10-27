# Third-party components and data

Coverage Detective's own code is MIT-licensed ([LICENSE](LICENSE)). Everything below keeps its own licence.
Processing steps and row counts for each dataset: [data/README.md](data/README.md) and the
[data report](docs/data_report.md).

## Non-commercial use

Speedtest by Ookla open data is licensed **CC BY-NC-SA 4.0**. Every speed figure, and everything derived from
it (gap zones, opportunities, scores, the committed extracts in `sample_data/` and the recorded runs in
`examples/`), is therefore for **non-commercial use** and shared alike. The pages show the attribution footer
for every source and it must be kept.

## Pre-existing components we integrate

| Component | From | Licence | How we use it |
|---|---|---|---|
| Meterless H-MEM reference engine (`vendor/meterless/hmem/`) | [Meterless/Meterless](https://github.com/Meterless/Meterless), `engines/hmem/reference`, commit `1742dec` | Apache 2.0 ([vendor/meterless/LICENSE](vendor/meterless/LICENSE)) | vendored unchanged; our `meterless_service/` serves it as the agents' shared case file (evidence, beliefs, revisions, critiques, notes, trust-ledger audit trail) |
| Meterless World Model reference engine (`vendor/meterless/world-model/`) | same repository, `engines/world-model/reference` | Apache 2.0 | vendored unchanged; places, zones, sites, operators, warnings, relationships and events per run |
| Zetaris Lightning (local Docker stack, not in this repository) | Zetaris | Zetaris licence (hackathon sponsor) | federated SQL: every dataset is its own source and every query is logged |
| Meterless Nightshift (local app, not in this repository) | Meterless | Meterless terms (hackathon sponsor) | the agents' AI provider: routes each model call to NVIDIA Nemotron and returns a signed receipt |
| NVIDIA Nemotron models (API, not in this repository) | NVIDIA, build.nvidia.com | NVIDIA API terms | the agents' model layer, reached through Nightshift (NVIDIA's API directly only as a fallback) |
| MapLibre GL JS 5.24 (CDN) | maplibre.org | BSD-3-Clause | maps |
| deck.gl 9.4 (CDN) | vis.gl | MIT | map layers |
| h3-js 4.5 (CDN) and h3 (Python) | Uber | Apache 2.0 | hexagon grid |
| OpenFreeMap basemap tiles | openfreemap.org | data © OpenStreetMap contributors, ODbL; OpenMapTiles schema (BSD / CC BY) | basemap |
| Manrope, IBM Plex Mono (Google Fonts) | Google Fonts | SIL Open Font License 1.1 | type |
| Python packages (`requirements.txt`), e.g. FastAPI, pandas, numpy, scipy, rasterio, pyosmium, DuckDB, httpx, pydantic, cryptography | PyPI | each package's licence (MIT, BSD, Apache 2.0) | backend |
| Node packages (`meterless_service/package.json`): tsx, TypeScript | npm | MIT / Apache 2.0 | runs the Meterless service |

## Data sources

| Source | What it gives us | Licence | Attribution |
|---|---|---|---|
| Speedtest by Ookla Global Fixed and Mobile Network Performance Maps (mobile tiles) | measured download / upload / latency, tests, devices per ~400 m tile, last 4 quarters | **CC BY-NC-SA 4.0** (non-commercial) | "Speedtest® by Ookla® Global Fixed and Mobile Network Performance Maps" |
| OpenCelliD | crowd-sourced radio cells: approximate mast sites, operators inferred from network codes | CC BY-SA 4.0 | "OpenCelliD Project, licensed under CC BY-SA 4.0" |
| OpenStreetMap (Geofabrik extracts) | mapped communication masts and towers; place names for zones | ODbL 1.0 | "© OpenStreetMap contributors" |
| Ofcom Wireless Telegraphy Register | operators' licensed microwave links: operators confirmed at sites, antenna heights | Ofcom copyright; free re-use if reproduced accurately and acknowledged | "Contains Ofcom data © Ofcom" |
| Ofcom Connected Nations (July 2025) | predicted 4G / 5G coverage by number of operators per constituency and council; per operator by nation | Open Government Licence v3.0 | "Contains Ofcom data © Ofcom" |
| ONS boundaries (countries, constituencies, local authorities) | which nation, constituency and council a cell is in | Open Government Licence v3.0 | "Source: Office for National Statistics licensed under the OGL v3.0; contains OS data © Crown copyright and database right" |
| WorldPop UK population | people per cell | CC BY 4.0 | "WorldPop (www.worldpop.org)" |
| Copernicus DEM GLO-30 | elevation, line of sight, candidate sites (crops in `sample_data/dem/`) | Copernicus DEM licence (free, attribution required) | "© DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018 provided under COPERNICUS by the European Union and ESA" |
| postcodes.io (live) | postcode / place lookup, nearest postcode | MIT software; ONS Postcode Directory and OS Open Names under OGL v3 | "Contains OS data © Crown copyright; Royal Mail data © Royal Mail copyright; ONS data © Crown copyright" |
| Open-Meteo (live) | weather now and next 24 h | CC BY 4.0 (non-commercial API use) | "Weather data by Open-Meteo.com" |
| Met Office severe weather warnings (live RSS) | warnings for the region | Crown copyright, Open Government Licence v3.0 | "Contains Met Office data © Crown copyright" |
| Environment Agency flood monitoring (live, England) | flood alerts and warnings within 10 km | Open Government Licence v3.0 | "Contains Environment Agency data licensed under the OGL v3.0" |
| Operator network codes (`data/reference/mcc_mnc_uk.csv`) | MCC/MNC → operator | compiled from public ITU / Ofcom numbering information | none required |

Saved live responses in `sample_data/` keep the licence of their source.
