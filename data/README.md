# Data sources

All data is mapped onto an H3 grid at resolution 7 (~5 km² cells) covering the UK.
Raw files live in `data/raw/` and built files in `data/processed/`; both are gitignored.

| Source | Version used | Licence | Raw file(s) | Built file(s) |
|---|---|---|---|---|
| ONS Countries boundaries | December 2023, UK BUC | Open Government Licence v3.0 | `raw/boundary/uk_countries.geojson` | `cells_r7.parquet` |
| WorldPop population | 2020, 1 km, UN-adjusted (GBR) | CC BY 4.0 | `raw/population/population.tif` | `cells_r7.parquet` |
| Ookla open data, mobile | 2025Q4–2026Q3 (latest: 2026Q3) | CC BY-NC-SA 4.0 | `raw/ookla/<quarter>_mobile_tiles_uk.parquet` | `ookla_mobile_r7.parquet`, `ookla_mobile_history_r7.parquet`, `ookla_mobile_tiles.parquet` |
| OpenCelliD cell towers | downloaded 25 Oct 2026 (cells updated Apr 2025 – Oct 2026) | CC BY-SA 4.0 | `raw/opencellid/234.csv.gz` | `masts_sites.parquet`, `masts_r7.parquet` |
| OpenStreetMap (Geofabrik extracts) | downloaded 25 Oct 2026 | ODbL 1.0 | `raw/osm/great-britain-latest.osm.pbf`, `ireland-and-northern-ireland-latest.osm.pbf` | `osm_masts.parquet`, `masts_sites.parquet`, `masts_r7.parquet` |
| Ofcom Connected Nations 2025, mobile coverage | reference date 1 July 2025 (release `202507_mobile_coverage_r01`) | Open Government Licence | `raw/ofcom/202507_mobile_coverage_r01.zip` | `ofcom_area_r7.parquet`, `ofcom_nations.parquet` |
| ONS Westminster Parliamentary Constituencies | July 2024, UK BGC | Open Government Licence v3.0 | `raw/boundary/pcon_2024.geojson` | `ofcom_area_r7.parquet` |
| ONS Local Authority Districts | December 2024, UK BGC | Open Government Licence v3.0 | `raw/boundary/lad_2024.geojson` | `ofcom_area_r7.parquet` |
| Ofcom Wireless Telegraphy Register (WTR) | downloaded 25 Oct 2026 (Last-Modified 25 Oct 2026 08:45 GMT) | Ofcom copyright; free re-use (see below) | `raw/ofcom_wtr/WTR.csv` | `wtr_points.parquet`, `masts_sites.parquet`, `masts_r7.parquet` |
| UK network codes (Wikipedia) | revision 1378731553, 5 Oct 2026 | CC BY-SA 4.0 | — | `reference/mcc_mnc_uk.csv` (committed) |
| Council and operator complaint contacts (outreach) | checked 26 Oct 2026 | Publisher's own site (see `source_url` in each row) | — | `reference/council_contacts.csv`, `reference/operator_contacts.csv` (committed) |

## Manifest

`data/manifest.json` is updated by every build script: per source, its data period, the
download date of each raw input, build time, refresh cadence, licence, and for each output
file its row count, size and SHA-256. `scripts/refresh.py` uses it to decide what is due.

## Zetaris sources

Each processed file is registered in Zetaris as its own source (a Lightning database with
one table) by `scripts/zetaris/register_sources.py`, read from the read-only mount
`/home/zetaris/customer-data/shared/coverage_detective/` (see the README).

| Zetaris source | Table | File | Rows |
|---|---|---|---|
| `CD_CELLS` | `cells_r7` | `cells_r7.parquet` | 56,592 |
| `CD_OOKLA_MOBILE` | `ookla_mobile_r7` | `ookla_mobile_r7.parquet` | 23,412 |
| `CD_OOKLA_MOBILE_HISTORY` | `ookla_mobile_history_r7` | `ookla_mobile_history_r7.parquet` | 87,725 |
| `CD_OOKLA_MOBILE_TILES` | `ookla_mobile_tiles` | `ookla_mobile_tiles.parquet` | 420,260 |
| `CD_MASTS_SITES` | `masts_sites` | `masts_sites.parquet` | 93,638 |
| `CD_MASTS` | `masts_r7` | `masts_r7.parquet` | 56,592 |
| `CD_WTR_POINTS` | `wtr_points` | `wtr_points.parquet` | 13,122 |
| `CD_OFCOM_AREA` | `ofcom_area_r7` | `ofcom_area_r7.parquet` | 56,592 |
| `CD_OFCOM_NATIONS` | `ofcom_nations` | `ofcom_nations.parquet` | 1,035 |
| `CD_TERRAIN` | `terrain_r7` | `terrain_r7.parquet` | 56,592 |
| `CD_LOS` | `los_r7` | `los_r7.parquet` | 55,577 |
| `CD_GAP_ZONES` | `gap_zones` | `gap_zones.parquet` | 677 |
| `CD_GAP_ZONE_CELLS` | `gap_zone_cells` | `gap_zone_cells.parquet` | 2,921 |
| `CD_OPP_ZONES` | `opportunity_zones` | `opportunity_zones.parquet` | 1,816 |
| `CD_OPP_ZONE_CELLS` | `opportunity_zone_cells` | `opportunity_zone_cells.parquet` | 8,832 |
| `CD_LIVE_EA_FLOODS` | `floods`, `flood_areas` | REST (live EA API, see below) | 136 / 4,208 on 25 Oct 2026 |

## ONS Countries boundaries

*Countries (December 2023) Boundaries UK BUC*, Office for National Statistics.
Source: Office for National Statistics licensed under the
[Open Government Licence v3.0](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/).
Contains OS data © Crown copyright and database right 2023.

## WorldPop

WorldPop (www.worldpop.org), *Global 2000–2020 1 km UN-adjusted population counts*,
United Kingdom, 2020:
https://data.worldpop.org/GIS/Population/Global_2000_2020_1km_UNadj/2020/GBR/gbr_ppp_2020_1km_Aggregated_UNadj.tif
Licence: [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).

**Processing** (`scripts/process/build_cells.py`): for each nation, every H3 r7 cell that
overlaps the boundary is kept, so coastal cells are kept too. Raster pixels inside the
boundary are summed into the cell holding each pixel's centre. A cell on a national border
goes to the nation where most of its people live (or, with nobody, the nation containing its centre).
Groups of connected cells with fewer than 500 people (small islands) are dropped.
Result: 56,592 cells, 67.41 million people.

## Ookla open data (mobile)

Speedtest® by Ookla® Global Fixed and Mobile Network Performance Maps, from the public S3
bucket `ookla-open-data` (`parquet/performance/type=mobile/`). Licence:
[CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/). Attribution:
"Speedtest® by Ookla® Global Fixed and Mobile Network Performance Maps. Based on analysis
by Ookla of Speedtest Intelligence® data for [quarter]. Provided by Ookla and accessed
[date]. Ookla trademarks used under license and reprinted with permission."
Non-commercial use only.

Each tile is a zoom-16 web-mercator quadkey (about 350–400 m across in the UK) with average download/upload (kbps),
average latency, number of tests and unique devices for the quarter. There is **no
time-of-day or weekday breakdown**, so congestion can only be inferred indirectly.

**Download** (`scripts/download/ookla.py`): the latest 4 quarters. Only the Parquet row
groups whose quadkey range can hold UK tiles, and only the needed columns, are fetched with
HTTP range requests (~20 MB of each ~185 MB file), then tiles are kept if their centre is in
the UK bounding box.

**Processing** (`scripts/process/build_ookla.py`): each tile's centre is mapped to its H3 r7
cell, and tiles outside the grid (Ireland, France, sea) are dropped. Per cell and quarter:
test-weighted mean download, upload and latency, total tests, devices and tiles.
- `ookla_mobile_r7.parquet`: latest quarter per cell; tests and devices per 1,000 people;
  trend over the 4 quarters (quarters with data, oldest download, % change, least-squares
  slope in Mbps per quarter when there are ≥ 3 quarters).
- `ookla_mobile_history_r7.parquet`: one row per (cell, quarter).
- `ookla_mobile_tiles.parquet`: every UK tile and quarter with its r7 cell, for variation
  within a cell and across neighbours.
- `ookla_meta.json`: quarters used.

Caveat: many cells have only 1–2 tests, so always report `tests` as the sample size.

## OpenCelliD

OpenCelliD (https://opencellid.org), the open database of cell towers by Unwired Labs.
Licence: [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/). File for MCC 234
(United Kingdom), downloaded with a free account token (`OPENCELLID_TOKEN`) from
`https://opencellid.org/ocid/downloads?type=mcc&file=234.csv.gz`. OpenCelliD publishes no
MCC 235 file ("FILE_NOT_FOUND"). The free file holds cells updated in roughly the last 18
months (145,365 rows on 25 Oct 2026).

Each row is one radio cell (a sector on one technology) with a position **estimated from
crowd-sourced measurements**, not the surveyed mast position. Coverage of the database is
uneven and incomplete, so the absence of masts is weaker evidence than their presence.

**Processing** (`scripts/process/build_masts.py`):
1. Keep GSM/UMTS/LTE/NR rows inside the UK bounding box.
2. Label each row with its operator from the network-code table below (MCC + MNC), kept
   as `operator_labels`. Codes the table marks as MVNO, GSM-R (Network Rail) or TETRA
   (Airwave) are dropped: they are not public mobile networks with their own masts. Codes
   not in the table are kept as `unknown (234-NN)`.
   Then group into the four networks with `data/reference/operator_groups.csv`
   (`operators`: EE / O2 / Vodafone / Three). Codes outside that table (Channel Islands,
   Isle of Man, legacy BT, small private networks) are group `other` and do not count as an
   operator. Vodafone and Three are kept separate although they merged as VodafoneThree in
   2025, because their networks still broadcast separate codes.
3. Cluster rows into approximate mast sites: rows are taken in order of most samples first,
   and each joins the nearest site leader within 150 m or starts a new site. This is leader
   clustering, which avoids chaining a dense city into one giant site.
4. Merge with the OpenStreetMap masts and the Ofcom licence register (below) into
   `masts_sites.parquet`.
5. Per grid cell centre (`masts_r7.parquet`): masts_within_5km, nearest_mast_km,
   nearest_mast_bearing_deg (from the cell towards the mast), nearest_mast_id / lat / lon /
   sources / operators / operators_confirmed / antenna_height_m,
   distinct_operators_within_5km, operators_within_5km, operators_confirmed_within_5km,
   masts_osm_only_within_5km, masts_known_operator_within_5km,
   masts_unknown_operator_within_5km (no operator from any source: OSM-only sites with no
   recognisable operator tag), wtr_sites_within_5km, microwave_backhaul_sites_within_5km,
   and operator_data_status:
   - `confirmed`: at least one operator within 5 km is confirmed by Ofcom's licence register;
   - `inferred`: operators known only from OpenCelliD / OSM;
   - `unknown`: sites found but no operator known;
   - `no_sites_found`.
   Populated cells, 25 Oct 2026: confirmed 44,009 cells (65.83 m people), inferred 3,895
   (1.18 m), unknown 2,143 (0.23 m), no_sites_found 5,530 (0.18 m). Use
   masts_unknown_operator_within_5km for "operators could not be identified for N sites".

## OpenStreetMap masts

Geofabrik extracts `great-britain-latest.osm.pbf` and
`ireland-and-northern-ireland-latest.osm.pbf` (https://download.geofabrik.de/europe/),
© OpenStreetMap contributors, [ODbL 1.0](https://opendatacommons.org/licenses/odbl/1-0/).
Downloaded by `scripts/download/osm.py`.

**Extraction** (`coverage_detective/sources/osm_masts.py`): nodes and closed ways tagged
`man_made=mast|tower` with `tower:type=communication`, `man_made=communications_tower`,
or `communication:mobile_phone=yes`; ways become the mean of their nodes. The `operator`
tag is kept as `osm_operator` and mapped to operator groups where recognisable: EE, O2,
Vodafone, Three by name; the shared-infrastructure companies MBNL → EE + Three and
Cornerstone / CTIL / Beacon → O2 + Vodafone. Neutral hosts (Arqiva, Cellnex, WIG) map to no
operator. `mobile_tagged` marks masts with `communication:mobile_phone=yes` or a recognised
mobile operator; other OSM communication masts may carry only broadcast or private radio.
Objects in both extracts (Northern Ireland) are kept once. Cached in `osm_masts.parquet`.

**Merge** (`coverage_detective/data/masts.py`, `masts_sites.parquet`):
1. Each OpenCelliD site joins its nearest OSM mast within 200 m. An OSM mast with matches
   becomes one site with `sources = opencellid,osm` at the OSM position (mapped, so usually
   more accurate than OpenCelliD's estimate). Unmatched sites keep `opencellid` or `osm`.
2. Ofcom register sites (below) join the nearest site within 200 m: it gains `ofcom_wtr` in
   `sources`, the register's operators in `operators_confirmed`, `has_microwave_backhaul`,
   `antenna_height_m`, and moves to the register's surveyed position if it had only
   OpenCelliD's estimate. Unmatched register sites become new sites (`sources = ofcom_wtr`).
Columns: site_id, lat, lon, sources (comma list), operators (= inferred + confirmed),
operators_inferred (OpenCelliD network codes, OSM operator tags), operators_confirmed (Ofcom
register), operator_labels, radio_types, cell_count, osm_id, osm_operator, mobile_tagged,
wtr_licensees, wtr_links, has_microwave_backhaul, antenna_height_m, cell_id.

Result (25 Oct 2026): 77,913 OpenCelliD sites from 144,681 radio cells; 13,644 OSM masts
(6,208 tagged mobile); 8,504 register sites (3,797 matched an existing site, 4,707 new);
93,638 sites in total (opencellid 74,352, osm 9,272, ofcom_wtr 4,707, ofcom_wtr+osm 2,446,
opencellid+osm 1,620, ofcom_wtr+opencellid 935, all three 306).
Sites per operator (confirmed by the register): Vodafone 35,815 (2,878), EE 29,649 (3,005),
O2 29,498 (3,594), Three 20,525 (3,005). These are site *clusters*, more than the real
number of masts: OpenCelliD estimates each operator's cells at slightly different
positions, so one shared mast often appears as several sites.

Caveat: OSM-only sites include broadcast-only towers, so `masts_osm_only_within_5km` is
reported separately and evidence should say which source a mast comes from.

List columns are comma-separated strings so any SQL engine (Zetaris) can read them.

## UK network codes (MCC/MNC → operator)

Wikipedia, *Mobile network codes in ITU region 2xx (Europe)*, section "United Kingdom – GB",
revision [1378731553](https://en.wikipedia.org/w/index.php?oldid=1378731553) (5 Oct 2026),
which cites the ITU Operational Bulletins and Ofcom's numbering allocations. Text licence:
[CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/). Built by
`scripts/download/mcc_mnc.py` into `data/reference/mcc_mnc_uk.csv` (81 codes): mcc, mnc,
label, brand, operator, status, bands, notes.

Operators are labelled **as the reference labels them** (`label` = Brand, or Operator when
Brand is empty), e.g. `EE`, `O2 (UK)`, `Vodafone UK`, `3`. Networks have merged in recent
years: EE absorbed T-Mobile and Orange (codes 30–34 are all EE), and Vodafone UK and Three
UK merged as VodafoneThree in 2025 but keep separate codes (15 and 20). So one current
company can appear under several labels, and labels are not current company names.

## Ofcom Connected Nations 2025: mobile coverage

Ofcom, *Connected Nations 2025* data downloads, mobile coverage, release
`202507_mobile_coverage_r01` (reference date **1 July 2025**). Provided by Ofcom "on an
open basis via the Open Government Licence"
([OGL v3.0](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/));
attribute as "Source: Ofcom, Connected Nations 2025". Definitions are from Ofcom's
[About this data: mobile coverage (2025)](https://www.ofcom.org.uk/siteassets/resources/documents/research-and-data/multi-sector/infrastructure-research/connected-nations-2025/about-this-data---mobile-coverage.pdf?v=407846)
(copy in `raw/ofcom/about-this-data-mobile-coverage-2025.pdf`).

This is **predicted** coverage from the four operators' signal-strength models (4G:
RSRP −105 dBm outdoor, −95 dBm indoor and in-car; 5G high confidence: SS-RSRP −110 dBm
outdoor), summarised per area. It is never coverage at an exact location.

Files in the zip: `_laua_` (361 local and unitary authorities), `_pcon_` (650 Westminster
constituencies), `_devcon_` (devolved constituencies), `_4G5G_` (combined 4G+5G at −105/−95
dBm, "at least one" / "all"), `_UK_and_nations_` (per operator: EE, O2, Three, Vodafone,
plus All / At least one / None, by nation and Urban / Rural / Total).

**Columns** `[Service]_[Coverage type]_[N]`: the percentage of premises (`prem_out`,
`prem_in`), land as 100 m pixels (`geo_out`), or road pixels (`abrd_in` A and B roads,
`mway_in` motorways, in-car) with coverage for that service from **exactly N** operators,
N = 0–4 (0–3 for 2G). Confirmed: each group of five sums to 100 (99.99–100.01). A blank
value is 0 when the rest of its group has values; a group that is entirely blank means
not applicable (e.g. no motorways in the area) and stays null.

**Processing** (`scripts/process/build_ofcom.py`, `coverage_detective/sources/ofcom.py`):
1. Boundaries (`scripts/download/ons_boundaries.py`, ONS Open Geography Portal, BGC =
   generalised, clipped to the coastline): Westminster Parliamentary Constituencies July
   2024, and Local Authority Districts December 2024 (May 2025 is published only as map
   tiles; there were no boundary changes between them). Codes checked against the Ofcom
   release: all 650 constituency and all 361 local authority codes match exactly.
2. Each cell centre is placed in its constituency and local authority (point in polygon in
   British National Grid); 3,421 coastal cells whose centre is offshore take the nearest area.
3. `ofcom_area_r7.parquet`, per cell: laua_code / laua_name, pcon_code / pcon_name,
   ofcom_area_level (`pcon`: every cell has constituency data, so the local-authority
   fallback is not needed), the constituency's pcon_prem_count / pcon_pixel_count and, for
   the families 4G premises outdoor / indoor, 4G geographic, 4G A/B roads, 4G motorways,
   5G high-confidence premises and geographic: `g4_prem_out_0..4`, `g4_prem_in_0..4`,
   `g4_geo_out_0..4`, `g4_abrd_in_0..4`, `g4_mway_in_0..4`, `g5hc_prem_out_0..4`,
   `g5hc_geo_out_0..4`, each with `_at_least_one` (= 100 − `_0`); plus the local
   authority's `laua_g4_geo_out_0` / `_1`; data_date `2025-07`.
4. `ofcom_nations.parquet`: the `_UK_and_nations_` file with tidy column names (location,
   technology, coverage_type, rurality, operator, coverage_pct, coverage_volume, date).

**Evidence wording**: "In <area>, X% of land has no outdoor 4G from any operator and Y% from
only one (Ofcom Connected Nations, July 2025)", with X = `g4_geo_out_0`, Y = `g4_geo_out_1`.

## Ofcom Wireless Telegraphy Register (WTR)

Ofcom, *Wireless Telegraphy Act Register*, https://www.ofcom.org.uk/spectrum/frequencies/wtr,
CSV https://static.ofcom.org.uk/static/radiolicensing/html/register/WTR.csv (205,372 rows,
downloaded 25 Oct 2026; Last-Modified 25 Oct 2026 08:45 GMT; SHA-256 in `data/manifest.json`).
Downloaded by `scripts/download/ofcom_wtr.py`.

**Re-use terms** (Ofcom, [Copyright and information re-use](https://www.ofcom.org.uk/about-ofcom/our-website/copyright)):
material "may be reproduced free of charge in any format or medium, provided that it is
reproduced accurately and not used in a misleading context. The material must be
acknowledged as Ofcom copyright and the title of the document/publication specified."
Attribute as "Contains data from Ofcom's Wireless Telegraphy Act Register, © Ofcom".
Ofcom notes it "cannot guarantee that the information will always be accurate or up-to-date",
and may withhold some licences for public-safety or national-security reasons.

**What it does and does not contain.** Mobile base stations are **not** in the register:
"Mobile network operators (MNOs) manage their own planning within blocks of assigned
spectrum" (Ofcom). For the mobile operators the register holds their **licensed fixed
links**: point-to-point microwave backhaul (6.5–83 GHz), each end with an exact location
(10-figure grid reference) and antenna height. Link ends are almost always at mast or hub
sites, so they *confirm* that an operator uses a site, but only sites with licensed
microwave backhaul (fibre-fed and self-licensed sites are missing).

**Operators** (`data/reference/wtr_operator_licensees.csv`, exact case-insensitive match on
the legal name, never loose words: "East Anglia Three Limited", "Cornerstone Security
Systems" and "Three Horseshoes Inn" must not count):
- Telefonica UK Limited → O2 (2,573 licences)
- Vodafone Limited → Vodafone (2,016)
- Mobile Broadband Network Limited (MBNL, the EE–Three network joint venture) → EE + Three (1,974)
- EE Limited, Hutchison 3G UK Limited, Cornerstone Telecommunications Infrastructure
  Limited (→ O2 + Vodafone) are listed but had no entries on 25 Oct 2026.
- Not mapped: British Telecommunications Limited (6,272 rows; BT owns EE, but these are
  mostly BT's fixed network) and Arqiva (broadcast and neutral host).

**Processing** (`scripts/process/build_wtr.py`, `coverage_detective/sources/ofcom_wtr.py`):
1. Keep live rows of the licensees above; drop satellite earth stations.
2. Location from the grid reference with pyproj: two letters = British National Grid
   (EPSG:27700), one letter = Irish Grid (EPSG:29903); 6-, 8- and 10-figure references,
   taken at the centre of the square they name. If a reference cannot be read, the
   register's own WGS84 lat/lon is used. Checked: the conversion agrees with the register's
   lat/lon to 0.1 m median (0.3 m p95) over 26,254 rows.
3. One point per (licence, location), i.e. each end of a link separately:
   `wtr_points.parquet` = point_id, link_id (licence number), lat, lon, location_from, ngr,
   licensee, operators, product, frequency_ghz, frequency_band, antenna_height_m,
   height_asl_m, station_types, register_rows, is_link_endpoint. 13,122 points; antenna
   heights median 20 m (15–26 m inter-quartile, max 203 m).
4. Points within 50 m are grouped into one register site (several links on one mast), then
   merged into the mast sites as above.

**Effect on populated cells** (55,577 cells, 25 Oct 2026):

| | Before the register | After |
|---|---|---|
| Sites found, but no operator identified for any | 11,591 cells (20.9%), 3.13 m people | 2,143 (3.9%), 0.23 m |
| Operators unidentified for at least one nearby site | 37,456 (67.4%), 63.52 m | 31,780 (57.2%), 61.47 m |
| No sites within 5 km | 10,648 (19.2%), 0.92 m | 5,530 (10.0%), 0.18 m |
| No named operator within 5 km | 22,245 (40.0%), 4.05 m | 7,677 (13.8%), 0.41 m |

People with an operator confirmed by the register within 5 km: EE 51.3 m, Three 51.3 m
(both via MBNL), Vodafone 44.0 m, O2 43.1 m.

## Copernicus DEM GLO-30 (terrain and line of sight)

- Source: Copernicus DEM GLO-30, public AWS bucket `copernicus-dem-30m`
  (https://copernicus-dem-30m.s3.amazonaws.com), one 1°×1° Cloud-Optimised GeoTIFF per tile,
  1 arc-second (~30 m; above 50°N the longitude spacing is 1.5 arc-seconds). 93 tiles,
  1.4 GB, in `data/raw/dem/` (`scripts/download/dem.py`; all-sea tiles do not exist and read
  as 0 m). Version: 2021 release, from TanDEM-X data acquired 2011–2015. Downloaded 25 Oct 2026.
- Licence: Copernicus DEM licence, free of charge worldwide with attribution: "© DLR e.V.
  2010-2014 and © Airbus Defence and Space GmbH 2014-2018 provided under COPERNICUS by the
  European Union and ESA; all rights reserved".
- It is a **surface** model (DSM): it includes some trees and buildings. Spot checks:
  Scafell Pike 973 m (true 978 m), Ben Nevis 1,342 m (1,345 m).
- Processing (`scripts/process/build_terrain.py`, `coverage_detective/data/terrain.py`):
  - `terrain_r7`: per r7 cell, elevations sampled (bilinear) at the centres of its 343 H3 r10
    children (~120 m apart): mean, min, max, range (max − min), std.
  - `line_of_sight()`: the path from the receiver (1.5 m above ground) to the antenna
    (antenna height from Ofcom's register when the site has one, else 25 m) is sampled every
    ~30 m. Earth curvature: the ground is raised by d1·d2 / (2kR), k = 4/3, R = 6,371 km.
    Obstruction = ground + bulge − line height; the largest is reported with its distance
    from the receiver and position (negative = clearance). blocked = obstruction > 0.
  - `los_r7`: every populated cell centre to its nearest approximate site (`masts_r7`),
    with `los_class` = clear / marginal (blocked by < 10 m, which may be trees or buildings
    in the surface model) / blocked (≥ 10 m). 25 Oct 2026: clear 29,555, marginal 10,153,
    blocked 15,869 cells; antenna height from the register for 40.2%. For the map only: the
    evidence API computes from the exact point.
- Limits: blocked means weaker signal (diffraction), not none; most antenna heights are
  assumed; no Fresnel zone, clutter or indoor loss; sites are approximate positions.

## OpenStreetMap places (zone names)

- Source: the same Geofabrik GB and Ireland extracts as the masts (downloaded 25 Oct 2026).
- Licence: ODbL 1.0, © OpenStreetMap contributors.
- Processing (`scripts/process/build_places.py`, `sources/osm_places.py`): nodes tagged
  `place=city|town|village|hamlet` with a name (English name preferred), inside the UK box;
  the same place seen in both extracts is kept once. 38,970 places, with their H3 r7 cell.

## Gap zones (derived)

`scripts/process/build_zones.py` → `gap_zones.parquet`, `gap_zone_cells.parquet`, from cells,
Ookla (latest quarter and history), mast sites, the DEM (line of sight) and Ofcom areas (local
authority per cell). Rules, thresholds and factors: README "Council view",
`coverage_detective/planning/zones.py` and `config.py`. 25 Oct 2026: 3,515 poorly served cells
before the zone threshold (slow 42, no site 1,346, blocked 2,128), 227 site-data-gap cells left
out, 677 zones of at least 50 people (264,561 people). Rebuild after Ookla or mast refreshes.

## Improvement opportunities (derived)

Same script, `opportunity_zones.parquet` and `opportunity_zone_cells.parquet`: populated cells (>= 10
people) that are NOT in a gap zone but have a measurable weakness, clustered like gap zones (zones of
>= 50 people). Reasons, computed in code (`coverage_detective/planning/opportunities.py`, thresholds in
`config.py`): `slow` (ring speed from >= 10 tests below half the lower of the local authority median and
the UK median), `falling` (download down >= 30% over >= 3 quarters), `few_operators` (only 1-2 operators
identified within 5 km and no unknown-operator sites nearby), `capacity` (>= 1,000 people, below 75% of
the UK median, and devices per head >= UK 90th percentile or a 20% fall). Zones with < 10 tests are
"limited data". 26 Oct 2026 (UK median 102.7 Mbps): slow 4,293 cells, falling 1,357, few_operators
3,656, capacity 947; 1,816 zones, 9.41 m people, 622 with limited data. Same sources and licences as
the gap zones (derived data).

## Sample-mode extracts (committed)

`scripts/build_samples.py` → `sample_data/zetaris/` (one Parquet per Zetaris source, cut to 12 km around each
sample place and the example zones' cells + 16 km; `extract.json` lists tables, boxes and row counts;
`uk_context.json` holds the UK-wide medians from the full data) and `sample_data/dem/` (Copernicus GLO-30
crops for the same boxes, int16 metres, native 30 m, deflate). 27 Oct 2026: 2,245 cells, 6,869 mast sites,
20,891 Ookla tiles, 2 gap zones and 4 opportunities; 9.6 MB in all, 8.2 MB of it DEM. Same sources and
licences as the full tables above (derived extracts; the DEM crops carry the Copernicus attribution). Used
only by sample mode (`python run.py --sample`, DuckDB in place of Zetaris) and the tests. Live samples for
the five sample places (`sample_data/<source>/`) were refreshed on 27 Oct 2026.

## Live sources (phase D)

Fetched when a question is asked, not stored. Clients in `coverage_detective/sources/`
(`live.py` does the fetching: 5 s timeout, one retry after 0.5 s, disk cache in
`data/cache/live/`). Every result carries `source`, `url`, `fetched_at`, `status`
(`live` / `cached` / `sample` / `not_covered` / `unavailable`), `data_period` and `as_of`.
Samples for the three sample places (real responses, 25 Oct 2026, 47 KB in all) are in
`sample_data/<source>/`, saved by `scripts/download/live_samples.py`; SAMPLE_MODE and the
tests use them. A storm (Bolelo) was passing at the time, so the samples hold active
warnings, which makes them useful test cases.

### postcodes.io

- URL: https://api.postcodes.io (`/postcodes/{pc}`, `/places?q=`, `/postcodes?lat=&lon=`).
  No key. Cache 30 days. Sample files: `postcodes_io/lookup_*.json`, `reverse_*.json`.
- Licence: postcodes.io is MIT-licensed software over open data: ONS Postcode Directory and
  OS Open Names (OGL v3; contains OS data © Crown copyright and database right; Royal Mail
  data © Royal Mail copyright and database right; National Statistics data © Crown copyright).
- Processing: a full UK postcode → postcodes endpoint (404 = not found, answered cleanly);
  anything else → places (OS Open Names), retrying with the longest word when nothing
  matches (results flagged `approximate`; "Great Langdale" is not in the list). Reverse:
  nearest postcode within 2 km, else postcodes.io's wide search (20 km). Each result gets
  its H3 r7 `cell_id`.

### Open-Meteo forecast

- URL: https://api.open-meteo.com/v1/forecast with `current` and `hourly` = precipitation,
  rain, snowfall, wind_speed_10m, wind_gusts_10m, weather_code; `forecast_hours=24`.
  No key. Cache 30 min.
- Licence: free for non-commercial use; data CC BY 4.0, attribution "Weather data by
  Open-Meteo.com" (shown in the explorer footer). UK forecasts blend national models
  including the Met Office UKV.
- Processing: `current` precipitation covers the last 15 minutes and is scaled to mm/h; the
  worst values in the next 24 hours are taken from the hourly forecast. Severity over now
  and the next 24 h:

  | Severity | Any of |
  |---|---|
  | severe | gusts ≥ 70 km/h, precipitation ≥ 8 mm/h, snowfall ≥ 1 cm/h, thunderstorm (WMO code 95–99) |
  | moderate | gusts ≥ 50 km/h, precipitation ≥ 2 mm/h, any snowfall |
  | none | otherwise |

  Only `severe` can support the `weather` cause.
- Zetaris: not registered. A Zetaris REST table can take per-query parameters
  (`${lat}` in the endpoint, filled by the hint `/*+ apiParam('lat', '54.4') */`), but
  creating the table calls the endpoint as written to infer its schema, and Open-Meteo's
  firewall rejects a literal `${` with HTTP 403 (so does the EA gateway). A fixed-location
  table would be no use for arbitrary points, so weather stays Python-only.

### Met Office severe weather warnings

- URL: https://www.metoffice.gov.uk/public/data/PWSCache/WarningsRSS/Region/{code} (public
  RSS, not DataHub; no key). Cache 15 min.
- Licence: © Crown copyright, Met Office; public sector information licensed under the
  Open Government Licence v3.
- Processing: the point's region comes from postcodes.io: the English region (`London` and
  `South East` → `se`), the nation for Wales (`wl`) and Northern Ireland (`ni`), and the
  council area for Scotland (`os`, `he`, `gr`, `st`, `ta`, `dg`; the full mapping is in
  `met_office_warnings.py`). From each item: level and types from the title ("Amber warning
  of rain, wind affecting …"), the listed local authorities and the validity ("valid from
  2000 Sat 24 Oct to 2100 Sun 25 Oct", UK local time → UTC). Expired warnings are dropped;
  `in_force` marks started ones. `district_listed` is true when the point's district,
  county or police-force area (which keeps the old county name, e.g. Cumbria) is in the
  list; a regional warning may not cover the point.

### Environment Agency flood monitoring

- URL: https://environment.data.gov.uk/flood-monitoring/id/floods?lat=&long=&dist=10.
  No key. Cache 15 min. England only.
- Licence: Open Government Licence v3; "This uses Environment Agency flood and river level
  data from the real-time data API (Beta)".
- Processing: keeps severity levels 1 (severe flood warning), 2 (flood warning) and 3
  (flood alert); level 4 (no longer in force) is dropped. Outside England the answer is
  `not_covered` with the message "not covered (England only for now)", never an empty
  list. SEPA, Natural Resources Wales and DfI Rivers are not wired in yet.
- Zetaris: registered as the live REST source `CD_LIVE_EA_FLOODS` (`register_ea_floods`),
  with tables `floods` (`/id/floods`: every alert and warning in England now) and
  `flood_areas` (`/id/floodAreas?_limit=10000`: each area's centre). Every query calls the
  EA API; `flood_warnings.zetaris_sql()` explodes both, joins on the area id and keeps
  areas whose centre is within 10 km (about 0.7 s). EA's own `dist` filter measures to
  the area polygon, which the REST tables do not have, so the centre-based query misses
  long river and coast areas (Great Langdale, 25 Oct 2026: the API found 2 alerts whose
  centres are 11 and 13 km away; Zetaris found 0). So for a point the explorer and
  `/api/live` use the API by default; the Zetaris path (`flood_via=zetaris`) suits
  England-wide or regional queries, e.g. joining warnings in force to cells.
