# Usage

The pages, the `POST /run` API, the sample inputs and outputs, and the emails and letters.

## Try it in 2 minutes (sample mode: no keys, no Zetaris, no Nightshift)

Needs Python 3.12 and Node 18+ (or just Docker).

```bash
git clone https://github.com/temilash/Coverage_find.git && cd Coverage_find
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
(cd meterless_service && npm install)     # the agents' memory service (Meterless H-MEM + World Model)
.venv/bin/python run.py --sample          # then open http://localhost:8000/signal
```

Or with Docker (sample mode is the image's default):

```bash
docker build -t coverage-detective .
docker run --rm -p 8000:8000 coverage-detective
```

Then click:

1. **CO9 1TP** (Gosfield, Essex): search `CO9 1TP` on My signal → **Diagnose with agents** → **Send this
   diagnosis** → Braintree Council or one of the four networks → **Write the email**.
2. **Great Langdale**: `/signal?lat=54.445&lon=-3.105` → **Diagnose with agents**. Hills block every network;
   open **Evidence** to see each item and the SQL behind it, then send the diagnosis to the council or to the network.
3. **Near Rhayader**: `/signal?lat=52.30&lon=-3.51` → Diagnose → **How the agents decided**: the critic
   challenges a belief, asks for a check, and a second round runs.
4. **Council view** → `/council?zone=Z00072` (**Ousby**) → Diagnose zone: the recommended fix, the candidate
   sites and **Today / With candidate 1** on the map. Also `/council?zone=Z00023` (**Broadstone**, a gap zone)
   and `/council?zone=O00004` (**Ludlow**, an improvement opportunity).

Or call it from a script (`POST /run` waits for the run and returns JSON):

```bash
curl -s -X POST localhost:8000/run -H 'content-type: application/json' -d @input_examples/example_1.json
curl -s -X POST localhost:8000/run -H 'content-type: application/json' -d '{"lat": 52.30, "lon": -3.51}'
curl -s -X POST localhost:8000/run -H 'content-type: application/json' -d '{"zone": "Z00072"}'
curl -s -X POST localhost:8000/run -H 'content-type: application/json' -d '{"place": "WC2N 5DU", "audience": "council officer"}'
```

The answer has the headline, the causes with confidence and the evidence ids behind each, what was ruled out,
the actions, the fix plan (zones), which provider and model answered each agent, and the path of the run's
trace. Bad input is a 422 with the reason; anything that cannot be answered is a JSON error with a message.

### What sample mode does, exactly

Sample mode runs the real code: the coordinator's loop, every tool, the evidence queries (DuckDB over small
committed extracts of each Zetaris source, `sample_data/zetaris/`, using the same SQL), line of sight on DEM
crops, the code guard and every Meterless write. What it does not have is Zetaris, live APIs (saved responses
are used, and say which place they were saved for) or a model key, so the agents' replies come from:

- **the 7 recorded examples** (`examples/*/llm_replies.json`): a request within 100 m of an example's point (or
  for an example zone) replays that example's recorded model replies, run at the recorded point, labelled
  "Recorded" in the UI and `"mode": "recorded replies (sample mode)"` in `/run` (they were recorded for the
  resident audience, so `audience` does not change a replay);
- **rules only, no model**, for any other point whose 10 km neighbourhood is inside the extracts: the same
  pipeline, with fixed rules over that run's evidence in place of each model call
  (`coverage_detective/agents/rules.py`), labelled as such;
- **a clear refusal** anywhere else (e.g. Oxford), listing the areas sample mode covers. Zones and area summaries
  without a recording are refused the same way.

Nothing maps an input to a prewritten answer: the recorded replies are model outputs keyed by example, agent,
round and call, and the evidence, guard and result are recomputed on every run.

## What it does

| My signal (`/signal`) | Council view (`/council`) | Explore (`/`) |
|---|---|---|
| ![](screenshots/hero_my_signal.png) | ![](screenshots/council/03_lake_district_gaps_and_opportunities.png) | ![](screenshots/explore.png) |
| One exact spot: likely causes ranked with confidence, what was ruled out, the likely best network, what to do, and an email to the council or to a network with the evidence attached. | **Gap zones** (poorly served people) and **improvement opportunities** (not poorly served, but a measurable weakness), ranked by a transparent score with adjustable weights; a fix view with candidate mast sites; agent diagnoses and a priority summary for any area. | The data itself, cell by cell: speeds, mast sites, operators, terrain and line of sight, Ofcom figures. |

| How the agents decided | The fix view | Every claim has its evidence |
|---|---|---|
| ![](screenshots/my_signal_how_agents_decided.png) | ![](screenshots/council/06_ousby_fix_with_candidate.png) | ![](screenshots/my_signal_evidence.png) |

## Examples: inputs, outputs and traces

[`input_examples/`](../input_examples/) has nine `POST /run` bodies and [`output_examples/`](../output_examples/) the
answers they produced on `run.py --sample` (saved by `scripts/save_output_examples.py`, with each run's trace).
The seven recorded examples, with full traces:

| Example | What it shows | Agents' headline | Rounds | Live run |
|---|---|---|---:|---:|
| [`great_langdale`](../examples/great_langdale/) | hills block every operator (terrain 0.70); evidence letters to the operator, council and MP | Hills between you and the nearest masts are the likely cause. | 1 | 81 s |
| [`near_rhayader`](../examples/near_rhayader/) | **the critic loop**: it asks for a check, a second round runs, a weak belief is downgraded | Terrain blocking EE and Three signals is the likely cause. | 2 | 180 s |
| [`central_london`](../examples/central_london/) | fast measured service: "no strong cause" (good data, nothing above 0.3) | No strong cause was found; the service measures well here. | 1 | 49 s |
| [`sutton_st_edmund`](../examples/sutton_st_edmund/) | flat Fens: distance, not terrain | Distance to the nearest mast is the likely cause. | 1 | 72 s |
| [`remote_powys`](../examples/remote_powys/) | **insufficient data**: no tests and no site within 10 km | Not enough data to say why: there are no measured speeds at this point or within 1 km and no mast sites in our data within 10 km, so operators could not be identified. | 1 | 54 s |
| [`zone_ousby`](../examples/zone_ousby/) | gap zone with no measurements: candidate sites, fix plan reviewed by the critic | No strong cause was found because there are no recent measurements. | 1 | 96 s |
| [`opportunity_windermere`](../examples/opportunity_windermere/) | improvement opportunity: capacity upgrade, opportunity wording checked | Busy-network capacity strain is the likely cause. | 1 | 92 s |
| [`summary_uk`](../examples/summary_uk/) | priority summary of the sample area (gap and opportunity sections) |  |  |  |
| [`summary_westmorland`](../examples/summary_westmorland/) | priority summary for one council |  |  |  |

Each folder has the result, the Meterless trace, the evidence with SQL, the World Model and the recorded
replies. They were recorded live on sample data on 27 Oct 2026, so `run.py --sample` replays them exactly.

## Sending the diagnosis

A finished diagnosis becomes an email to the resident's council, found from the point's local authority, or to
one of the four networks. **Write the email** asks the letter writer for a draft to that recipient by name, the
guard checks it, and code adds the address, the subject, a signature with the postcode, coordinates and the
diagnosis reference, and the `mailto` link. **Open in email app** hands the message to the resident's own mail
client, and the app never sends anything itself. **Copy message** and **Download .md** cover the rest.

The addresses come from `data/reference/council_contacts.csv` (22 councils) and `operator_contacts.csv`. Each row
records the page that published the address and the date someone checked it, and
`scripts/check_outreach.py` re-checks the rows. EE and O2 publish complaints email addresses; Three and Vodafone
publish a complaints form and a postal address only, so those get the form, the address and Copy, and the panel
says there is no email rather than inventing one. A council missing from the table gets gov.uk's council finder,
never a guessed address.

![The email to the council](screenshots/signal_agents_great_langdale_email_council.png)
![No published address: the complaints form and the postal address](screenshots/signal_agents_great_langdale_email_operator_form.png)

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
