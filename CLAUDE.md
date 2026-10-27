# Coverage Detective

Open Agent Hackathon project (deadline Tue 27 Oct 2026, 23:45 UK). A multi-agent tool that
explains **why mobile signal is poor** at a UK location from real public data, and ranks
coverage gaps for operators and councils. Scope: mobile networks only (4G/5G, phones,
mobile data), not home broadband.

Sponsor tech: **Zetaris** (federated SQL, local Docker) — every dataset the agents use is
registered there as a separate source and queried through it. **Meterless**: the H-MEM and World
Model engines are the agents' shared case file (meterless_service/), and **Meterless Nightshift**
is the primary AI provider (NVIDIA Nemotron through it; NVIDIA direct only as a labelled fallback).

## Product context

Hackathon track: "The Agent That Can Explain Why". Judging: Impact 30, Technical 20,
Innovation 15, Demo 15, Product & UX 10, Sponsor Tech 10. Judges reward agents that
critique each other, planner/executor/evaluator loops, shared memory, retries and visible
traces; they penalise linear pipelines, prompt wrappers, hardcoded demo paths and
unsupported claims.

Two modes:
1. **Why is my signal bad?** postcode/place → ranked likely causes with confidence,
   evidence for each, causes ruled out, actions.
2. **Where are the gaps?** region → ranked under-served areas with main cause, people
   affected and suggested fix.

Agents (built; coordinator.py, agents.py): Coordinator (routing, max 3 rounds, fallbacks), Investigator
(Zetaris + live APIs → evidence items), Diagnostician (hypotheses from a fixed cause set:
distance_to_mast, terrain_blocking, single_operator, capacity_strain, weather,
active_incident, insufficient_data), Critic (approves / asks for more
evidence / downgrades), Advisor (audience-specific explanation), Gap analyst (screening
query → parallel diagnoses → group by cause). Meterless holds the world model and each
agent's beliefs with an audit trail; Zetaris is the evidence layer.

**capacity_strain** (not "peak congestion": Ookla open data has no time-of-day or weekday
breakdown, so congestion can only be inferred): speeds are low although coverage and
nearby masts look adequate. Evidence: speeds declining over the last 3–4 quarters, high
tests/devices per head (e.g. visitor areas), masts nearby with no terrain block.

**single_operator** comes from the mast sites (OpenCelliD / OSM / Ofcom register), not Ofcom
coverage: word findings as "only N operators have approximate mast sites within 5 km", never
"only N operators cover this area", and lower confidence when few sites are found.

Evidence strength: OpenCelliD is crowd-sourced and incomplete, so "masts found" is stronger
evidence than "no masts found".

**Confirmed vs inferred operators**: `operators_confirmed` come from Ofcom's Wireless
Telegraphy Register (licensed microwave links at the site): say "confirmed by Ofcom's
licence register". `operators_inferred` come from OpenCelliD network codes / OSM tags: say
"inferred from crowd-sourced data". The register lists fixed links, not base stations, so a
site missing from it is not evidence of anything. operator_data_status per cell:
confirmed / inferred / unknown / no_sites_found.

**Mast wording rules** (evidence findings and agent output):
- Say "approximate mast sites", never a count of "masts": sites are clusters of
  crowd-sourced radio cells and mapped towers, and one real mast can appear as several.
- Use `operator_data_status` (per cell, `CD_MASTS.masts_r7`): `confirmed` (an operator within
  5 km is confirmed by Ofcom's licence register), `inferred` (operators only from crowd-sourced
  data), `unknown` (sites found, no operators known), `no_sites_found`. Whenever
  `masts_unknown_operator_within_5km` > 0, say "operators could not be identified for N nearby
  sites"; never imply fewer operators are present.
- `no_sites_found` is weak evidence: OpenCelliD's free data is incomplete.

Ofcom: the postcode-level API is not available to us (`sources/ofcom_api.py` is a stub that
returns None; None means "no Ofcom data", not "no coverage"). Ofcom Connected Nations area
data (July 2025, constituency / local authority; phase E) is regional context only, never
coverage at the exact location. Wording: "In <area>, X% of land has no outdoor 4G from any
operator and Y% from only one (Ofcom Connected Nations, July 2025)" (X = g4_geo_out_0,
Y = g4_geo_out_1). The Advisor's resident
actions must include "Check your address on Ofcom's mobile coverage checker"
(https://www.ofcom.org.uk/mobile-coverage-checker).

**Freshness**: every evidence item carries `data_period` and `as_of`. Stored tables: from
`data/manifest.json` via `manifest.evidence_meta(<zetaris table>)`; live sources: the fetch
time (`manifest.live_meta()`). Every build script must call `manifest.record(...)`.
`scripts/refresh.py` refreshes stored sources on their cadence (Ookla new quarter,
OpenCelliD weekly, OSM monthly, Ofcom new release; WorldPop/ONS/DEM manual).

Principles: code and SQL compute every number, the LLM only interprets; every claim links
to an evidence item; causes are "likely" with confidence; sample/offline mode for
reproducible runs.

Limitations (state them in outputs):
- Causes are inferred, not confirmed (operators' internal data isn't public).
- Ookla data is aggregated by quarter with no time-of-day detail, so congestion can only
  be inferred indirectly.
- No address-level Ofcom coverage: the Ofcom API is not available to us, and Ofcom's
  local-authority figures (regional context only) are predicted, not measured.
- OpenCelliD mast data is crowd-sourced and incomplete, and positions are estimated from
  measurements, so sites are approximate. Operators come from network codes, and networks
  have merged (EE = T-Mobile + Orange; Vodafone UK + Three UK = VodafoneThree, 2025), so
  labels follow the reference table, not current company names.
- Mobile networks only; home broadband is out of scope.
- Terrain: blocked means weaker, not no signal; antenna heights mostly assumed (25 m); GLO-30 is
  a surface model (includes some trees and buildings), most reliable for hills and valleys.

## Layout

```
coverage_detective/     package
  config.py             paths, .env loading, H3_RES=7, UK_BBOX, SAMPLE_LOCATIONS, sample_mode()
  evidence.py           evidence bundle for a point (Zetaris queries, line of sight, live sources)
  planning/             zones.py (gap zones), opportunities.py (improvement opportunities), scoring.py
                        (priority / opportunity score), siting.py (fixes, candidates)
  memory/meterless.py   Meterless client (H-MEM + World Model over HTTP; in-memory fallback)
  data/sample_db.py     sample mode: DuckDB over sample_data/zetaris/ in place of Zetaris
  agents/               llm.py (Nightshift first, NVIDIA direct fallback; receipts), schemas.py (pydantic),
                        tools.py (tools -> Meterless), agents.py (the agents), guard.py (code guard),
                        prompts/*.md (frozen: hash in examples/index.json), coordinator.py (the loop:
                        diagnose_point, diagnose_zone; progress; export), replay.py (recorded replies for
                        sample mode), rules.py (sample mode away from the examples: rules, no model),
                        trace_log.py (logs/run_<id>.jsonl + stdout)
meterless_service/      Node HTTP service on :8100 over vendor/meterless (H-MEM, World Model); data/meterless/
vendor/meterless/       Meterless reference engines, vendored unchanged (Apache 2.0)
  data/
    grid.py             H3 r7 cells over the UK + WorldPop population
    masts.py            merge OpenCelliD / OSM / Ofcom register sites; per-cell mast stats
    terrain.py          Copernicus DEM reader + line_of_sight()
    queries.py          cell_sql(): the cross-source cell query (explorer + evidence)
    manifest.py         data/manifest.json: data periods, refresh cadence, evidence_meta()
    zetaris.py          Zetaris client (query / execute, token refresh, query log)
  sources/
    ookla.py, opencellid.py, osm_masts.py, ofcom.py, ofcom_wtr.py     stored sources
    live.py             shared live fetching (timeout, retry, cache, sample fallback)
    geocode.py, weather.py, met_office_warnings.py, flood_warnings.py live sources
    ofcom_api.py        stub (Ofcom postcode API not available; returns None)
  api/app.py            FastAPI: explorer API + /api/live + /api/los + POST /api/evidence; pages /, /signal, /council
  api/council.py        /api/council/* (scopes, ranked zones, CSV, zone detail with fixes)
  api/agents.py         agent runs: /api/diagnose, /api/runs/{id}, letters, emails, zone runs, priority
                        summary, POST /run (synchronous, JSON)
scripts/download/       fetch raw data into data/raw/<source>/ (skip files that exist)
scripts/process/        build data/processed/*.parquet (+ build_web_layers.py for the map)
scripts/zetaris/        compose.sh (data mount), register_sources.py (one function per source)
scripts/check_data.py   the data report through Zetaris (docs/data_report.md); --phases for the old A/B/E checks
scripts/refresh.py      refresh stored sources on their cadence
scripts/screenshots.py, council_screenshots.py  README screenshots -> docs/screenshots/
scripts/nightshift_probe.py   one request through Nightshift (routed model, receipt, signature)
scripts/test_meterless.sh     Meterless Engine tests (H-MEM, World Model, service, client: 26/26)
scripts/record_examples.py, save_output_examples.py   examples/ (live model), output_examples/ (from /run)
tests/                  pytest (166 tests; offline: samples, mocked Zetaris / DEM / LLM)
input_examples/, output_examples/   POST /run bodies and the answers they produced (the app never reads them)
Dockerfile, .dockerignore   Python 3.12 + Node 20, SAMPLE_MODE=true by default, port 8000
docs/SUBMISSION.md      HackOS submission text
web/                    pages, no build step (MapLibre 5.24 + deck.gl 9.4 + h3-js 4.5):
                        index.html + app.js (Explore), signal.html + signal.js (My signal),
                        council.html + council.js (Council view), agents.js (agent UI, shared), style.css
run.py                  start the app on :8000 (checks Zetaris; --sample or SAMPLE_MODE=true; --host)
data/raw/               raw inputs and downloads (gitignored)
data/processed/         built Parquet files (gitignored)
data/web/, data/cache/  generated map layers, live-call cache (gitignored)
sample_data/           live responses + evidence bundles for the sample places (committed)
data/reference/         small reference tables (committed): mcc_mnc_uk.csv,
                        operator_groups.csv, wtr_operator_licensees.csv
data/manifest.json      data versions (committed)
```

## Zetaris (how it works here)

- Stack: `~/zetaris-platform` (Docker). Health: its `./verify.sh`. Run compose through
  `scripts/zetaris/compose.sh` (adds our read-only data mount; a plain `docker compose up`
  there drops it).
- Local files are readable only under `/home/zetaris/customer-data/shared/`
  (`zetaris_local_file_sytem_root_path`); data/processed is mounted there as
  `coverage_detective/`. Other paths: "Invalid file path to access".
- Client: `coverage_detective/data/zetaris.py`. `query(sql)` = SELECT only
  (`/query/sql/start` + `/query/sql/page`); `execute(sql)` = DDL such as
  `CREATE LIGHTNING ...` (`/sql-editor/sqls/run`). Sign-in at `ZETARIS_URL`
  (`http://localhost:3000`) `/api/auth/login` → `idToken` + `orgId`. All values come back
  as strings (the client converts numbers) and rows do not keep SELECT column order.
  Every query is logged to `logs/zetaris_queries.jsonl`.
- One Lightning database per dataset (`CD_*`), registered by
  `scripts/zetaris/register_sources.py` (one function per source; drop + recreate).
  No `IF EXISTS`; descriptions allow only letters, digits, spaces and `_ . - ,`;
  `SHOW DATABASES` is disabled (use `SHOW TABLES IN <db>`).
- REST sources: `CREATE LIGHTNING REST TABLE t FROM DB REQUEST(endpoint "...", method "GET",
  http_encoding "URLENCODED", response_type "JSON", insecure_ssl "false") HEADER() BODY()`; each
  query calls the endpoint and the JSON becomes one row (explode arrays in SQL). `${name}` in
  the endpoint is filled per query by `/*+ apiParam('name', 'value') */`, but CREATE calls the
  endpoint literally to infer the schema, and Open-Meteo / EA gateways 403 a literal `${`. So
  only unparameterised feeds work: `CD_LIVE_EA_FLOODS` (floods + flood_areas).
- After a Docker upgrade, containers on the old network could not reach each other; the
  fix was `sudo aa-remove-unknown`, then `docker compose down` (no `-v`) and `up -d`.

## Running

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env                                  # then fill in (never print or commit it)
scripts/zetaris/compose.sh up -d driver               # Zetaris with our read-only data mount

# Stored data (build order)
.venv/bin/python scripts/process/build_cells.py       # cells_r7 (needs the two hand-placed inputs below)
.venv/bin/python scripts/download/ookla.py            # UK extracts of the last 4 quarters (range reads)
.venv/bin/python scripts/process/build_ookla.py       # ookla_mobile_r7 / _history_r7 / _tiles
.venv/bin/python scripts/download/mcc_mnc.py          # operator reference (already committed)
.venv/bin/python scripts/download/opencellid.py       # needs OPENCELLID_TOKEN
.venv/bin/python scripts/download/osm.py              # Geofabrik GB + Ireland (~2.6 GB)
.venv/bin/python scripts/process/build_masts.py       # masts_sites, masts_r7
.venv/bin/python scripts/download/ofcom_wtr.py        # Ofcom Wireless Telegraphy Register (~54 MB)
.venv/bin/python scripts/process/build_wtr.py         # wtr_points, then rebuilds the mast tables
.venv/bin/python scripts/download/ons_boundaries.py   # constituencies 2024 + local authorities Dec 2024
.venv/bin/python scripts/process/build_ofcom.py       # ofcom_area_r7, ofcom_nations (zip in data/raw/ofcom/)
.venv/bin/python scripts/download/dem.py              # Copernicus GLO-30 tiles (~1.4 GB)
.venv/bin/python scripts/process/build_terrain.py     # terrain_r7, los_r7 (~2 min; --los = los only)
.venv/bin/python scripts/process/build_places.py      # OSM place names (for zone names)
.venv/bin/python scripts/process/build_zones.py       # gap_zones, gap_zone_cells (~30 s)
.venv/bin/python scripts/zetaris/register_sources.py  # all CD_* sources (or name some)
.venv/bin/python scripts/download/live_samples.py     # refresh the live samples in sample_data/

# Checks
.venv/bin/python scripts/check_data.py                # A, B, E through Zetaris
.venv/bin/python scripts/refresh.py --check-only      # which stored sources are due
.venv/bin/python -m pytest -q

# Explorer
.venv/bin/python scripts/process/build_web_layers.py  # after any data change
.venv/bin/python run.py                               # http://localhost:8000 (+ Meterless on :8100)
.venv/bin/python run.py --sample                      # no Zetaris / Nightshift / key (Meterless on :8101)
.venv/bin/python scripts/nightshift_probe.py --schema # Nightshift check (needs METERLESS_RUNTIME_TOKEN or _FILE)
scripts/test_meterless.sh                             # Meterless Engine tests
(cd meterless_service && npm install)                 # once: Node deps for the Meterless service
.venv/bin/python scripts/screenshots.py --chrome ~/.cache/ms-playwright/chromium-1223/chrome-linux64/chrome
```

Inputs placed by hand: `data/raw/boundary/uk_countries.geojson` (ONS countries, all four
nations) and `data/raw/population/population.tif` (WorldPop UK).

## Status (27 Oct 2026, afternoon)

Built in phases; after each: run checks, commit, push, stop for review. Order agreed:
A → B → E → freshness → explorer → WTR → D → C → evidence API → F → final check_data.py;
G (power cuts) dropped as future work; H (visitor load) only if time. Then the agents.

### Done (commit)

- **A. Zetaris foundation** (`d4f80c9`): client, data mount, `CD_CELLS` (56,592 r7 cells,
  67.41 m people) and three Ookla sources. Ookla open data, 2026Q3 + 3 previous quarters,
  read by HTTP range requests of the UK row groups: 23,412 cells with tests, 87,725
  cell-quarters, 420,260 tiles (zoom 16, ~350–400 m). Test-weighted averages; sample sizes
  always carried.
- **B. Masts** (`f6f3889`, `c29b4a2`): OpenCelliD cells clustered at 150 m (MVNO / GSM-R /
  TETRA excluded) + OSM communication masts (two-pass pyosmium), merged within 200 m.
  Per-cell stats in `masts_r7`, operator_data_status and the wording rules above.
- **E. Ofcom regional** (`6afb9c6`): Connected Nations July 2025, constituency per cell
  (`CD_OFCOM_AREA`, with the local authority figures) and per operator by nation
  (`CD_OFCOM_NATIONS`). Blanks = 0 unless the whole group is blank.
- **Freshness** (`d07f9c1`): manifest, `refresh.py` (Ookla quarterly, OpenCelliD weekly,
  OSM and WTR monthly, Ofcom new release, cells / DEM manual), evidence_meta().
- **Data explorer** (`cc0cf3d`, `1095848`, `dfd8cb8`): FastAPI + MapLibre/deck.gl; 12
  layers, mast sites from zoom 10, Ookla tiles from zoom 12, cell panel from one Zetaris
  query, search, phone layout. UK map limits were tried and removed at the user's request
  (they broke scrolling). The Zetaris client signs in again before its 15-minute token expires.
- **Ofcom Wireless Telegraphy Register** (`ae28592`): base stations are NOT in it; it holds
  the operators' licensed microwave links. Licensees matched by exact legal name
  (Telefonica → O2, Vodafone, MBNL → EE + Three; BT and Arqiva not mapped). 13,122 points,
  8,504 sites (3,797 matched existing sites, 4,707 new) → 93,638 sites. Populated cells with
  sites but no known operator fell from 20.9% to 3.9%. Antenna heights for line of sight.
- **D. Live sources** (`5261816`): `sources/live.py` (5 s timeout, one retry, disk cache
  `data/cache/live/`, fallback cached → sample, status live / cached / sample / not_covered
  / unavailable) + geocode (postcodes.io, 30 days), weather (Open-Meteo, 30 min; severity
  none / moderate / severe: gusts ≥ 70 km/h, rain ≥ 8 mm/h, snow ≥ 1 cm/h or thunder =
  severe; only severe supports the `weather` cause), Met Office warnings (public RSS by
  region, 15 min; `district_listed`), EA floods (England only, 15 min; "not covered" outside
  England, never an empty list). `/api/live`, `/api/geocode`, `/api/geocode/reverse`;
  explorer "Right now" section; attribution footer. Zetaris REST: `CD_LIVE_EA_FLOODS` works
  (centre-based, so the point answer uses EA's polygon-aware API); Open-Meteo cannot be
  registered (see Zetaris notes).
- **C. Terrain** (`c3caa01`): `data/terrain.py` (bilinear DEM, ≤ 4 tiles in memory,
  `line_of_sight()` with 30 m samples, k = 4/3 curvature, antenna height from the register
  else 25 m). `CD_TERRAIN.terrain_r7` (elevation per cell from 343 r10 samples) and
  `CD_LOS.los_r7` (cell centre → nearest site, map layer only; clear 29,555 / marginal < 10 m
  10,153 / blocked 15,869 populated cells; register height for 40%). Everything a user or
  agent sees for a location uses the EXACT point, never the cell centre: the evidence API
  (to each operator's nearest site) and the explorer panel (`/api/los?lat=&lon=`, nearest
  site to the clicked / searched point; map line + elevation profile). Great Langdale:
  blocked by 288 m, 2.25 km towards the site.
  Mast refreshes rebuild `los_r7`.
- **Evidence API** (`d9cbd90`): `POST /api/evidence` {query} or {lat, lon} →
  `evidence.py`: Q1 `cell_sql`, Q2 sites within 10 km of the exact point, Q3 UK context
  (cached), all logged in the bundle; line of sight to the nearest site of each operator
  within 10 km (+ nearest of any operator); live items. Items: id, source, data_period,
  as_of, query_or_url, cell_ids, finding, value, sample_size, strength (strong / moderate /
  weak), bears_on. Bundles for the sample places saved in `sample_data/evidence/`.

- **Pages, phase 1: My signal** (26 Oct): `/signal`. Evidence bundle gained Q4 (Ookla tiles
  within 1 km), `operators` (per operator: nearest site, confirmed / inferred, line of sight
  from the exact point, mast height measured / assumed) and `likely_best` (rule: clear first,
  nearest, confirmed on ties; else least obstructed; `reviewed_by_agents: false`). Map draws each
  operator's line (own colour; red dashed when blocked; obstruction marked). Agent placeholders:
  "Explanation (agents)", disabled "Diagnose with agents" / "Draft evidence letter".
- **Levels of detail** (26 Oct): Explore = area level (hexagons; cell panel shows cell aggregates
  only, line-of-sight grade from the cell centre, "Data details" SQL, "Check an exact location
  here" → /signal?lat&lon). My signal = point level (no hex colouring: Ookla tiles within 2 km,
  sites within 10 km marked confirmed / inferred / unknown, hexagon outline, per-operator lines,
  "See this area in Explore"). Council view = zones.
- **Council view** (26 Oct): gap zones (build_zones.py; rules in planning/zones.py: (a) < 5 Mbps
  over cell + ring from >= 10 tests, (b) no site within 5 km unless >= 10 tests measure >= 5 Mbps
  = "site data gap", (c) < 10 tests and terrain blocking every site within 5 km; cells >= 10 people,
  zones >= 50 people (user's choice, so sparse valleys like Great Langdale count); 677 zones),
  scoring.py (rank-normalised factors within the scope, weights adjustable, contributions;
  "need" omitted until deprivation is built), siting.py (rule-based fix + DEM high points and
  existing sites by people gaining a clear line), api/council.py, /council page (scope picker,
  sliders, ranked table, map, zone panel, CSV, agent placeholders). Zones link to Explore
  (?zone= outline) and My signal (centroid). CD_GAP_ZONES, CD_GAP_ZONE_CELLS.

- **Agents, phase 1: Meterless service** (26 Oct): vendored H-MEM + World Model reference engines
  (their tests pass); meterless_service/server.ts on :8100 (runs, evidence, beliefs with
  ledger revisions, critiques, notes, audit trail, diagnoses near a point; World Model entities,
  operator-context relationships, events); Python client with fallback; run.py starts it.
  Agent plan (user's brief): investigator / diagnostician / critic / advisor / letter writer /
  fix planner, loop of max 3 rounds via the shared case file in Meterless, code guard, NVIDIA
  Nemotron via an OpenAI-compatible API (LLM_BASE_URL, LLM_API_KEY, LLM_MODEL).
- **Agents, phase 2: tools, schemas, Nemotron client** (26 Oct). Models: LLM_MODEL =
  nemotron-3.5-super-vl (fast: investigator tool calling, advisor / letters / fix planner with
  strict json_schema, thinking off); LLM_MODEL_REASONING = nemotron-3-ultra (diagnostician,
  critic: thinking ON, JSON schema in the prompt, validated by pydantic). Probe results: Ultra under
  strict json_schema degenerates on long prompts; Super with thinking is slow/flaky; guided_json is
  rejected; nemotron-3-super (non-3.5) is 404 for this account. The endpoint has bursts of HTTP 500:
  llm.py retries once, then the other model answers (`fallback` on the result). Raw reasoning is
  never stored. Tools (agents/tools.py): speeds_near, speed_trend, sites_near, line_of_sight(op),
  terrain, ofcom_area, live_conditions, prior_diagnoses_nearby, zone_summary, candidate_sites
  (+ network_comparison, code only); each writes items with stable ids to Meterless (with their
  Zetaris SQL) and logs a tool_call note. Check: scripts/check_agent_tools.py.
- **Agents, phase 3: agents + guard** (26 Oct): agents.py (investigate, diagnose, critique, advise,
  write_letter, plan_fix, review_fix, advise_zone), prompts/*.md (short, 1-2 examples each).
  Guard after every agent: unknown evidence ids removed (belief dropped if nothing supports it),
  weak-only beliefs capped at 0.5, numbers must match a value in the cited evidence within rounding /
  unit changes (else one rewrite by the same agent, then the sentence is removed); all recorded as
  "guard" notes. LLM: 5xx retried after 2 s then 6 s, then the other model; retries and switches are
  in "llm_call" notes. Code also enforces: round-1 checks, max 8 calls a round, no repeated checks,
  round-3 needs_evidence -> downgrade, mandatory resident actions, one ranking entry per operator,
  letter sources listed from evidence. Timings (26 Oct): investigator ~3 s, diagnostician ~19 s,
  critic ~40 s (Ultra), advisor / letter ~5 s, fix planner ~2 s. Check: scripts/check_agents.py.
- **Agents, phase 4: coordinator** (26 Oct): coordinator.py. Point: create run, World Model place
  (+ area), prior_diagnoses_nearby, loop (investigate -> diagnose -> critique; next round only if the
  critic requested new checks; max 3), World Model sites per operator (nearest_site{op} with the
  line-of-sight result), warnings, advisor, record_diagnosis + diagnosis_completed event. Zone: same
  loop on zone tools, then fix planner + critic review (max one revision), fix_plan_proposed event,
  candidate sites in the World Model. An agent failing twice -> status "partial" built from the
  evidence. Progress: one step per LLM call and tool call in data/runs/<run_id>.json (and Meterless
  run progress); full output in data/runs/<run_id>.full.json. Prompts: critic has a time-fit check
  (short-lived causes cannot explain long-standing poor service; downgrade to <= 0.3), diagnostician
  caps weather / active_incident at 0.4 when poor service is long-standing. Guard rewrites use the
  same agent prompt on the fast model. Meterless payloads can no longer overwrite record fields.
  Check: scripts/check_coordinator.py (summary in data/runs/phase4_summary.json).
- **Phase 4 review fixes** (26 Oct, user's go-ahead): diagnostician moved to 3.5 Super (strict json_schema;
  DIAGNOSTICIAN_ULTRA_ROUNDS in agents.py can put rounds back on Ultra), critic stays on Ultra; calls with
  thinking on time out at 90 s (45 s otherwise). Guard rules now in code, not only prompts: weakness is decided
  by code (`guard.weak_reason`: labelled weak, Ookla < 30 tests, sites crowd-sourced only, sites with unknown
  operators) for the 0.5 cap; [prior_diagnoses] is context only (stripped from belief evidence, and not shown
  to the critic, which kept using it as support); a confidence change without a revision reason is discarded
  ("unexplained_change"). Prompts: critic (b) must downgrade weak beliefs above 0.5, (e) only calls service
  long-standing when the trend shows it; diagnostician: 30+ tests above the UK median -> no problem cause
  above 0.3; advisor: "service measures well here" headline. Check (examples/compare/): wall clock 63-171 s
  (Phase 4: 130-272 s; one run 284 s from fast-model timeouts), diagnostician 3-19 s (was 49-100 s); London now
  "No strong cause was found; the service measures well here". examples/great_langdale/ re-recorded from
  run_20261026213702_fgoz.
- **Agents, phase 5: pages** (26 Oct): api/agents.py: POST /api/diagnose and POST
  /api/council/zones/{id}/diagnose start a run in a thread (max 2 at once, later ones "queued") and return
  run_id; GET /api/runs/{id} gives progress steps (always) and, once finished (or ?detail=1), the trace
  (round cards built from Meterless notes, critiques, belief history), evidence (with Zetaris SQL) and the
  run's World Model; POST /api/runs/{id}/letter {recipient}; POST /api/council/summary {scope, weights}
  (priority summary of the top zones by the advisor, zone rows as evidence items so the guard checks its
  numbers, plus any agent diagnoses of those zones from Meterless). web/agents.js (shared): progress strip
  (one chip per agent call, tool calls grouped, retries / model switches outlined, guard rewrites labelled),
  causes with confidence bars (hover / focus: old -> new with the reason), evidence tags that open the item
  and its SQL, round cards, a small World Model graph, Markdown and download helpers. My signal: "Diagnose
  with agents" (audience picker), headline, causes, ruled out, agents' best network, actions, editable
  letter with .md download, trace; `?run=` reopens a run. Council view: "Diagnose zone (agents)" (cause,
  recommended fix with the chosen candidate outlined on the map and people from its evidence, critic notes,
  trace), "Priority summary (agents)" for the area. Guard rewrite calls carry `purpose`. Screenshots:
  `scripts/screenshots.py --agents` (live runs, docs/screenshots/*agents*).
- **Panel UI cleanup** (26 Oct, layout and wording only; no agent logic or prompt changes): one tabbed run view
  in agents.js (`AG.runView`) used by both pages: Result (headline, recommended-fix card or best-network card,
  "Why we think this" = top 3 causes with bars and one plain sentence, ruled out; citations are small numbers
  that open the Evidence tab), How the agents decided (timeline, one collapsed card per round with a one-line
  summary, plain-language steps, critic text trimmed, guard corrections merged "x3"; models, timings, function
  names and guard codes behind "Technical details", off by default; total run time once), Evidence (numbered
  cards with Zetaris SQL, data details, World Model graph at the bottom, 12px labels). Council panel: 440px,
  sticky header (name, close, tabs), body scrolls inside, collapse handle; My signal: 440px column with the
  same handle. Spacing tokens 4/8/12/16/24, font sizes 13/14/17. Zone table: data-confidence dot with tooltip
  (label only for good data), names on one line with "id · council" truncated below, short headers with
  tooltips. Priority summary: ranked list built from the zone data (name, cause tag, "836 of 873 people", id as
  muted text); the API now returns `zone_rows`. Advisor citations: the reviewed beliefs are listed with id and
  cause name, cause names are mapped to belief ids (`guard.map_cause_citations`), and the guard accepts belief
  ids from the advisor (numbers checked against the belief's evidence). Before/after screenshots:
  `scripts/ui_screenshots.py before|after [--summary]` -> docs/screenshots/ui_cleanup/.
- **Council view: improvement opportunities, zoom levels, area picker, fix view** (26 Oct): second zone type
  (O00001; planning/opportunities.py, built by build_zones.py, `CD_OPP_ZONES` / `CD_OPP_ZONE_CELLS`; reasons
  in code: slow / falling / few_operators / capacity, all listed; "limited data" under 10 tests; opportunity
  score with its own factors in scoring.py, teal scale). `GET /api/council/view?bbox&zoom&scope&show` is the one
  source for the map and the list (caps in `config.COUNCIL_VIEW`: zoom < 10 top 40 gap zones in view, no
  opportunities; >= 10 up to 100 gap zones + 60 opportunities; a selected zone is always kept; cap notes).
  Scopes now carry gap / opportunity counts, people and bounds (`picker_counts`). Page: "Analyse area"
  combobox (Whole UK, What's on the map now, nations, councils), Gaps | Opportunities | Both, map toggle,
  per-row Diagnose, "Diagnose the top 3 in view" (one at a time), priority summary of what is listed
  (gap and opportunity sections; `priority_summary.md` now takes the zone kind as a template value).
  Agents: zone type line in the inputs; mode line in fix_planner.md / fix_critic.md; coordinator
  `wording_check` (opportunities must say "opportunity", never "gap" / "no coverage" / "no service";
  "not a (confirmed) gap" allowed); the fix planner's candidate must be a cand_/existing_ item (a cited
  [fix_rule] is cleared, guard note "not_a_candidate"). Fix view: zone cells coloured by outcome for the
  selected candidate (green gains a line, hatched still blocked, muted not tested), lines only for the
  selected candidate (nearest 12, hover for one), numbered pins, star for the agents' pick, label pills,
  dashed 250 m site areas, existing sites with operator rings, candidate cards (people reached, nearest
  existing site, ground height), Today / With candidate N with people counts, fit with room for the panel,
  Reset view. Opportunity run (Windermere O00332, examples/opportunity_windermere/): 196 s, 1 round.
  Screenshots: `scripts/council_screenshots.py` -> docs/screenshots/council/.
- **Repo cleanup and final README** (27 Oct): removed development-only files (examples/phase4/ and
  compare/, the phase check scripts check_agents / check_agent_tools / check_coordinator / check_memory /
  check_evidence / check_live / check_terrain / check_council, ui_screenshots.py, the saved evidence bundles,
  old screenshots, data/check_data_report.json); data/samples/ moved to sample_data/; LICENSE (MIT, our code),
  THIRD_PARTY.md (data licences, vendored Meterless engines), full .env.example (every variable, empty);
  README rewritten for judges; setup and pipeline details moved to docs/technical.md; screenshots regenerated
  (scripts/screenshots.py reopens the recorded runs with ?run=). The phase entries below mention scripts
  that no longer exist; they are in the git history.
- **Agents, phase 6: sample mode, examples, data report** (27 Oct): `python run.py --sample` runs with no
  Zetaris, no live calls and no LLM key: `data/sample_db.py` (DuckDB views named like the Zetaris sources over
  `sample_data/zetaris/*.parquet`; percentile_approx / collect_list / named_struct translated; UK medians
  from uk_context.json), `terrain.SampleDEM` (crops in sample_data/dem/), saved live responses (5 sample
  places), recorded agent replies (`agents/replay.py`: keyed example + agent + round + call index, letters per
  recipient; missing key -> ReplayMissing "no recorded reply for this step", never a live call; results
  `replayed`, labelled "Recorded" in the UI, sample-mode banner); its own Meterless (8101,
  data/meterless_sample/). Extracts: `scripts/build_samples.py` (9.6 MB, 8.2 MB DEM). Examples:
  `scripts/record_examples.py` (sample data + live model, Meterless 8102) -> 7 runs + 2 summaries,
  `examples/index.json` with the prompts' hash (prompts frozen: a test checks it). Prompt change before the
  freeze: critic.md (b) an assumed antenna height only weakens a blocked line under ~30 m, cite the margin;
  line-of-sight evidence carries `obstruction_margin_m`. Great Langdale x3 after it: terrain 0.70 / 0.75 /
  0.75, the critic still objected once (weather, asked for a speed trend). Guard: region-wide Met Office
  warnings are weak. Code rules: point headline "Not enough data to say why: ..." when insufficient_data is
  top (or tied) and nothing is measured; a fixed "Operators could not be identified" limitation; point
  wording check (never "no coverage"); best-network card says none can be ranked when no operator has a
  site. `scripts/check_data.py` -> docs/data_report.md (every Zetaris source: rows, query time, period, age,
  refresh, licence; one cross-source row per sample place; live sources). Council view 500s fixed (empty
  scopes, all-null factors). README rewritten (architecture, loop, guard, pages, sample mode, examples).

- **Submission checks** (27 Oct, branch `submission-checks`): Nightshift as the primary provider in llm.py
  (Bearer token from METERLESS_RUNTIME_TOKEN or its proxy-token file, `meterless-app: coverage-detective`,
  `meterless-scope` per run, 180 s; routed_model / receipt_id / route / latency / Ed25519 signature from the
  `meterless` object into every llm_call note; meterless/auto and bare ids refused; STRICT_MODEL=1; NVIDIA
  direct only on unreachable / 401 / 5xx, as a `provider_fallback` note). Header names and the receipt shape come
  from two working Nightshift 1.2.3 integrations (the user's information), still to be confirmed by a live probe.
  Sample mode: replay only within 100 m of an example (run at the recorded point; at 1 km the guard stripped 14
  sentences of another place's numbers), rules only (agents/rules.py) elsewhere inside the extracts, a refusal
  listing the areas outside them; a saved postcode > 2 km away is not reported; saved live samples name their
  place. POST /run. JSONL trace per run (the Meterless client now records reads per agent). input_examples/ +
  output_examples/ from real /run calls. Dockerfile (built and run here: pages, /run, traces OK). README,
  technical.md, THIRD_PARTY, SUBMISSION.md. The committed example replies are still the NVIDIA-direct
  recordings of 27 Oct 00:26 until scripts/record_examples.py is re-run through Nightshift.

### Zetaris sources

`CD_CELLS`, `CD_OOKLA_MOBILE`, `CD_OOKLA_MOBILE_HISTORY`, `CD_OOKLA_MOBILE_TILES`,
`CD_MASTS_SITES`, `CD_MASTS`, `CD_WTR_POINTS`, `CD_OFCOM_AREA`, `CD_OFCOM_NATIONS`,
`CD_TERRAIN`, `CD_LOS`, `CD_GAP_ZONES`, `CD_GAP_ZONE_CELLS`, `CD_OPP_ZONES`, `CD_OPP_ZONE_CELLS` (15 Parquet)
and `CD_LIVE_EA_FLOODS` (live REST). Row counts are in
data/README.md.

### Sample places (what the data says, 25 Oct)

- Great Langdale (54.445, -3.105): no Ookla tests in its cell; nearest site 3.8 km SE; EE,
  Three, Vodafone inferred, none confirmed; every operator's nearest site blocked by
  terrain (129–288 m). Storm Bolelo: severe gusts, flood alerts nearby.
- Central London (WC2N 5DU): 133.6 Mbps from 1,425 tests; all four operators confirmed;
  devices per head far above the UK median (visitors).
- Near Rhayader (52.30, -3.51): 14.4 Mbps from 25 tests, down 35% over 4 quarters; only
  Vodafone within 5 km (confirmed), 2 sites with unknown operators; EE / Three site 6.5 km,
  blocked by 137 m; no O2 site within 10 km (weak); floods not covered (Wales).
- Flat check village: Sutton St Edmund (Lincolnshire Fens), line of sight clear.

### Known limits and open questions

- OpenCelliD's free file is incomplete; postcodes.io's place list lacks "Great Langdale"
  (by name it resolves approximately to Little Langdale: use coordinates).
- Our data is far coarser than Ofcom's own checker (address level); suggested retrying for
  an Ofcom API key.
- 26 Oct: the Zetaris stack was found removed (containers gone); restarted with
  `scripts/zetaris/compose.sh up -d` (volumes kept, sources intact). Earlier the driver was
  restarted without the mount: always use compose.sh.
- Top-ranked zones mostly have low data confidence (0-5 tests): the score is about need, the
  confidence column shows how sure we are. Northern Ireland has many zones (thin crowd-sourced
  site data there may contribute).
- Met Office warnings cover whole regions: should "elsewhere in the region" warnings count
  as weaker evidence for `weather`? (asked the user, no answer yet)
- 47% of populated cells look blocked from their centre; many margins are small (surface
  model), hence the marginal class.

### Next

1. Live checks on the user's machine: `scripts/nightshift_probe.py --schema` (routed model, receipt, both
   structured-output styles), then re-record the examples through Nightshift (`scripts/record_examples.py`,
   ~15-20 min) and re-save `output_examples/` and `examples/near_rhayader/agent_trace.jsonl`; update the README
   limitation about NVIDIA-direct recordings once done.
2. F. Rural–urban classification + deprivation (deciles within each nation) → `deprivation_r7`,
   `CD_DEPRIVATION` (`queries.py` already joins it when the manifest has "deprivation").
3. H. Visitor load, if time. A comparison with Ofcom's checker (not done; listed as future work).

## Rules

- Never commit secrets: `.env` is gitignored, `.env.example` has placeholders only.
- Raw downloads go in `data/raw/` (gitignored). Committed sample data stays small (`sample_data/`).
- Record every source's licence and processing steps in data/README.md.
- Always carry sample sizes (tests, devices) with Ookla figures; many cells have 1–2 tests.
- Scripts are re-runnable: skip downloads that already exist; `--force` rebuilds.
- Simple, readable code. If an external source or Zetaris is blocked or slow, say so
  with options rather than working around it silently.
