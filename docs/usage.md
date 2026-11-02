# Usage

The pages, the API and sample mode.

## Sample mode

```bash
.venv/bin/python run.py --sample          # then open http://localhost:8000/signal
```

Sample mode runs the real code (the same SQL, line of sight on DEM crops, the same rules) over small committed
extracts of every table (`sample_data/db/`), DEM crops (`sample_data/dem/`) and saved live responses. A point
must lie inside the extracts with its 10 km neighbourhood; anywhere else the API answers with a clear 404 that
lists the areas covered. Try:

1. **Great Langdale**: `/signal?lat=54.445&lon=-3.105`. Hills block the line to every network's nearest site.
2. **CO9 1TP** (Gosfield, Essex): search `CO9 1TP`, then **Send this diagnosis** to Braintree Council or a network.
3. **Near Rhayader**: `/signal?lat=52.30&lon=-3.51`. Only Vodafone has a site within 5 km; speeds have fallen.
4. **Council view**: `/council?zone=Z00072` (Ousby): the suggested fix, the candidate sites and
   **Today / With candidate 1** on the map. Also `Z00023` (Broadstone) and `O00004` (Ludlow, an opportunity).

## My signal (`/signal`)

One exact point (`?q=LD6 5AD` or `?lat=52.30&lon=-3.51`). It calls `POST /api/evidence` and shows:

- **Why: likely causes**: each cause from the fixed set (terrain blocking, distance to mast, single operator,
  capacity strain, weather, active incident, insufficient data) with its rule score from 0 to 1, a plain
  sentence, and the evidence items behind it (`coverage_detective/diagnosis.py`). A score says how strongly the
  public data points to a cause; it is not a probability. Causes resting only on weak evidence score at most 0.5;
  where 30+ tests measure at or above the UK median, no cause scores above 0.3. What the data rules out
  (calm weather, no flood alerts) is listed separately.
- **What to do**, always starting with Ofcom's mobile coverage checker for the address.
- **Send this diagnosis**: an email to the council (from the point's local authority) or to EE, O2, Three or
  Vodafone, at the address each one publishes (`data/reference/*_contacts.csv`). The letter is a fixed template
  filled from the diagnosis; every figure in it is quoted from an evidence finding, with its source and date.
  The app never sends anything: it opens your email app, or you copy the text.
- **Networks compared**: for each operator, the nearest identified site, whether the operator is confirmed by
  Ofcom's licence register or inferred from crowd-sourced data, and the line of sight from the exact point
  (clear / marginal / blocked, how far the ground rises above the line, mast height measured or assumed).
- **Likely best network here** (a rule: clear lines first, nearest first; otherwise the least obstructed).
- Measured speeds within 1 km, in the cell and in the neighbouring cells, with test counts; the 4-quarter trend;
  Ofcom's area figures; current weather, Met Office warnings and flood warnings.
- Every evidence item, every SQL query behind it, and the limitations.

## Council view (`/council`)

Gap zones (poorly served people) and improvement opportunities (not poorly served, but a measurable weakness),
ranked by a transparent score with adjustable weights (`planning/scoring.py`). The list always matches the map
(`GET /api/council/view`). Opening a zone shows the rule-based fix (`planning/siting.py`: new site, add operators,
capacity upgrade or gather measurements), up to three candidate sites ranked by the people in the zone who gain a
clear line of sight, the zone's figures, and the SQL behind them. CSV export of the ranking.

How zones are built (`scripts/process/build_zones.py`, rules in `planning/zones.py`, thresholds in `config.py`):

- A cell with at least 10 people is **poorly served** if (a) its test-weighted download over the cell and neighbour
  ring is below 5 Mbps from at least 10 tests, or (b) no approximate site lies within 5 km, unless at least 10
  tests measure at least 5 Mbps (then it is a gap in our site data, not in coverage), or (c) it has fewer than 10
  tests and terrain blocks the line (by 10 m or more) to every site within 5 km.
- **Zones** are connected clusters of poorly served cells with at least 50 people in total.
- **Improvement opportunities**: speed well below the area or UK median, falling speeds, only 1-2 operators
  identified, or a busy-network signal (`planning/opportunities.py`); "limited data" under 10 tests.
- **Priority score**: people, severity, persistence, worsening trend and few operators, each normalised by rank
  within the chosen area and weighted (defaults 35 / 25 / 15 / 10 / 15). The API returns each contribution.
- **Fixes**: up to 30 DEM high points within 5 km of the zone and the existing sites within 10 km are checked by
  line of sight (25 m mast, 1.5 m receiver) to every populated cell within 8 km. No planning, land, power or
  backhaul checks.

## Explore (`/`)

The data itself, cell by cell (H3 resolution 7, about 5 km²): speeds, tests, trend, population, nearest site,
operators within 5 km, operator data status, Ofcom figures, elevation and line of sight from the cell centre.
Mast sites appear from zoom 10, Ookla tiles from zoom 12. The layer "Model estimate: best signal"
shows the radio model's estimated RSRP for the regions built so far, with a legend saying it is a model
estimate, not a measurement ([radio_model.md](radio_model.md)). Clicking a cell runs one cross-source query, shown with
its SQL. "Check an exact location here" opens My signal at that point.

## API

OpenAPI docs at `/docs`.

| Endpoint | What it returns |
|---|---|
| `POST /api/diagnose` `{"query"}` or `{"lat", "lon"}` | headline, scored causes with their evidence ids, ruled out, best network, actions, limitations, the evidence items and the SQL |
| `POST /api/evidence` (same body) | the full evidence bundle (items, operators, sites, queries) with its `diagnosis` |
| `POST /api/email` `{"lat", "lon", "to", "sent_by"}` | the email for `council`, `EE`, `O2`, `Three` or `Vodafone`: subject, text, HTML, mailto link; send `body_markdown` to use an edited letter |
| `GET /api/outreach/recipients?district=` | the council and the four networks, with how to reach each |
| `GET /api/council/view?bbox=&zoom=&scope=&show=` | the zones on the map now |
| `GET /api/council/zones?scope=&type=&weights=`, `/zones.csv` | ranked zones with each factor's contribution |
| `GET /api/council/zones/{zone_id}` | one zone: cells, figures, fix, candidate sites |
| `GET /api/radio/meta` | radio model: regions modelled, assumptions, RSRP classes (model estimates, not measurements) |
| `GET /api/radio/cells?bbox=&operator=` | estimated RSRP per modelled cell in a bbox, best server or one operator |
| `GET /api/radio/cell/{cell_id}` | one cell's estimate: best server and each operator (site, distance, diffraction) |
| `GET /api/cell/{cell_id}` | everything about one H3 r7 cell, from one query |
| `GET /api/los?lat=&lon=(&site_id=)` | line of sight from the exact point to a site (default the nearest) |
| `GET /api/live?lat=&lon=` | weather, Met Office warnings, EA flood warnings |
| `GET /api/geocode?q=`, `/api/geocode/reverse?lat=&lon=` | postcodes.io lookups |
| `GET /api/layers`, `/api/layer/{name}`, `/api/masts?bbox=`, `/api/tiles?bbox=`, `/health` | map data and status |

```bash
curl -s -X POST localhost:8000/api/diagnose -H 'content-type: application/json' -d '{"lat": 54.445, "lon": -3.105}'
curl -s -X POST localhost:8000/api/email -H 'content-type: application/json' \
  -d '{"lat": 54.445, "lon": -3.105, "to": "council", "sent_by": "A. Resident"}'
```

## The evidence bundle

`coverage_detective/evidence.py` gathers, for one point: its H3 cell and neighbours; four logged DuckDB queries
(Q1 the cross-source cell query, Q2 mast sites within 10 km, Q3 UK medians, Q4 Ookla tiles within 2 km); line of
sight from the exact point to the nearest site of each operator within 10 km; and the live sources. Each item has
`id`, `source`, `data_period`, `as_of`, `query_or_url`, `cell_ids`, `finding` (one sentence built in code),
`value`, `sample_size`, `strength` (strong / moderate / weak) and `bears_on` (the causes it is relevant to).

Strength, in short: Ookla speed is strong from 50 tests and weak under 10; line of sight is strong when blocked
by 30 m or more and weak when the margin is under 10 m (trees or buildings in the surface model); "no sites found"
and operator gaps are weak (crowd-sourced data is incomplete); live items are weak when served from a sample.
