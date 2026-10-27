# Coverage Detective

**AI agents that explain why your mobile signal is bad, and show councils where to act, from real public data,
with every claim traced to its evidence and SQL.**

Open Agent Hackathon, **Track 2: The Agent That Can Explain Why**.

![My signal: the agents' diagnosis for Great Langdale](docs/screenshots/hero_my_signal.png)

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

1. **Great Langdale**: `/signal?lat=54.445&lon=-3.105` → **Diagnose with agents**. Hills block every network;
   open **Evidence** to see each item and the SQL behind it, then send the diagnosis to the council or to the network.
2. **Near Rhayader**: `/signal?lat=52.30&lon=-3.51` → Diagnose → **How the agents decided**: the critic
   challenges a belief, asks for a check, and a second round runs.
3. **Council view** → `/council?zone=Z00072` (**Ousby**) → Diagnose zone: the recommended fix, the candidate
   sites and **Today / With candidate 1** on the map.

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

## The problem

Many people in the UK still have poor mobile signal where they live (our data finds 677 poorly served gap
zones, home to about 265,000 people), but nobody tells them **why**: is it the hills, the distance to the mast,
a single network, a busy cell, or today's storm? Operators' network data is private, Ofcom's checker predicts
coverage but does not explain it, and councils deciding where to push for new masts have spreadsheets, not
reasons.

- **Residents**: "why is my signal bad here, which network is likely best, what can I do?", plus a letter to
  send with the evidence attached.
- **Councils**: "which places are worst served, why, what fix would help most people, and where are the
  cheaper upgrade opportunities?"

## What it does

| My signal (`/signal`) | Council view (`/council`) | Explore (`/`) |
|---|---|---|
| ![](docs/screenshots/hero_my_signal.png) | ![](docs/screenshots/council/03_lake_district_gaps_and_opportunities.png) | ![](docs/screenshots/explore.png) |
| One exact spot: likely causes ranked with confidence, what was ruled out, the likely best network, what to do, and an email to the council or to a network with the evidence attached. | **Gap zones** (poorly served people) and **improvement opportunities** (not poorly served, but a measurable weakness), ranked by a transparent score with adjustable weights; a fix view with candidate mast sites; agent diagnoses and a priority summary for any area. | The data itself, cell by cell: speeds, mast sites, operators, terrain and line of sight, Ofcom figures. |

| How the agents decided | The fix view | Every claim has its evidence |
|---|---|---|
| ![](docs/screenshots/my_signal_how_agents_decided.png) | ![](docs/screenshots/council/06_ousby_fix_with_candidate.png) | ![](docs/screenshots/my_signal_evidence.png) |

## Sending the diagnosis

A finished diagnosis becomes an email to the resident's council, found from the point's local authority, or to
one of the four networks. **Write the email** asks the letter writer for a draft to that recipient by name, the
guard checks it, and code adds the address, the subject, a signature with the postcode, coordinates and the
diagnosis reference, and the `mailto` link. **Open in email app** hands the message to the resident's own mail
client, and the app never sends anything itself. **Copy message** and **Download .md** cover the rest.

The addresses come from `data/reference/council_contacts.csv` (21 councils) and `operator_contacts.csv`. Each row
records the page that published the address and the date someone checked it, and
`scripts/check_outreach.py` re-checks the rows. EE and O2 publish complaints email addresses; Three and Vodafone
publish a complaints form and a postal address only, so those get the form, the address and Copy, and the panel
says there is no email rather than inventing one. A council missing from the table gets gov.uk's council finder,
never a guessed address.

![The email to the council](docs/screenshots/signal_agents_great_langdale_email_council.png)
![No published address: the complaints form and the postal address](docs/screenshots/signal_agents_great_langdale_email_operator_form.png)

## How it works

```
 public data ──► Zetaris ──► evidence API ──► agents ◄──► Meterless ──► pages, POST /run
 (Ookla, masts,   (one        (findings with   (investigate,   (H-MEM case      (My signal,
  Ofcom, DEM,      source per   strength, data   diagnose,       file + World     Council view,
  live APIs)       dataset)     period and SQL)  critique, act)  Model)           Explore)
                                      ▲                │  ▲
                                      └── code guard ──┘  └── Nemotron via Meterless Nightshift
```

**The agent loop** (`coverage_detective/agents/coordinator.py`), up to 3 rounds:

```
investigate ─► diagnose ─► critique ─► the critic asked for checks and round < 3? ─ yes ─► investigate again
                                            │ no
                                            ▼
                     act: advisor (a place)  /  fix planner ⇄ critic review (a zone)
```

The agents (`coverage_detective/agents/agents.py`, prompts in `agents/prompts/`):

- **Coordinator** (code): runs the loop, enforces the round-1 checks and the critic's requests, caps calls,
  writes the World Model, recalls earlier diagnoses nearby, falls back to a partial result if an agent fails twice.
- **Investigator** picks tools (speeds, trend, sites, line of sight per operator, terrain, Ofcom area, live
  conditions, earlier diagnoses); each writes evidence items with stable ids, a strength and their SQL.
- **Diagnostician** ranks causes from a fixed set (distance to mast, terrain, single operator, capacity strain,
  weather, active incident, insufficient data) with confidence, citing evidence ids.
- **Critic** approves, asks for specific checks (another round), downgrades or rejects, with reasons: are the
  citations real, is the evidence strong enough, was an alternative left unchecked, does the cause fit the
  time pattern (a storm cannot explain a year of slow speeds).
- **Advisor** writes the answer for a resident, council officer or operator; for zones the **fix planner**
  proposes a fix from the candidate sites and the critic reviews it; the **letter writer** drafts the email.
- Agents never message each other: each reads and writes the run's case file in Meterless H-MEM.

**The code guard** runs after every agent and records every intervention: citations to evidence that does not
exist are removed (a belief left with none is dropped); a belief resting only on weak evidence (under 30 tests,
crowd-sourced sites, unknown operators, a region-wide warning) is capped at 0.5; every number must match the
cited evidence (one rewrite, then the sentence is removed); earlier diagnoses are context, never support; a
confidence change needs a reason; opportunities are never called gaps.

**"Insufficient data" is a valid answer.** Where nothing is measured and no site is known, the answer says so
(`remote_powys`: "Not enough data to say why ...") instead of guessing, and "no site in our data" is never
presented as "no coverage".

## Sponsor tech

**Zetaris: the evidence layer.** Every dataset is its own Zetaris Lightning source (15 Parquet sources such as
`CD_CELLS`, `CD_OOKLA_MOBILE`, `CD_MASTS_SITES`, `CD_OFCOM_AREA`, `CD_LOS`, `CD_GAP_ZONES`, plus the live REST
source `CD_LIVE_EA_FLOODS`), and the agents' tools query them with federated SQL: one query per place joins
cells, Ookla speeds and history, masts, Ofcom, terrain and line of sight. Every query is logged with the
evidence it produced, so each claim on screen opens to its SQL. Row counts, freshness and query times
(71-576 ms per source, 366-467 ms for the cross-source query per place): [docs/data_report.md](docs/data_report.md).
Setup is scripted: `scripts/zetaris/compose.sh up -d` (adds our read-only data mount) and
`scripts/zetaris/register_sources.py` (registers every source; re-runnable).

**Meterless Engines: H-MEM and World Model, the shared case file.** We run the open-source Meterless **H-MEM**
and **World Model** reference engines (Apache 2.0, vendored unchanged in `vendor/meterless/`) behind our
`meterless_service/` (Node, port 8100), which `run.py` starts.

| Engine | What it does here | Feature it powers |
|---|---|---|
| H-MEM | each run's case file: evidence, beliefs, critiques, notes; every belief revision is a trust-ledger update (who, old → new confidence, why); diagnoses near a point | the agents' only channel to each other; "How the agents decided" is built from its ledger; earlier diagnoses recalled as context |
| World Model | places and zones, sites per operator with the line-of-sight result, warnings, candidate sites, events (`diagnosis_completed`, `fix_plan_proposed`) | the World Model graph in the Evidence tab; the fix view's candidate sites |

Tests: `scripts/test_meterless.sh` runs the H-MEM engine's suite (10/10 passed), the World Model engine's
suite (10/10), our service over both (4/4) and the Python client with its in-memory fallback (2/2): **26/26
passed** on 27 Oct 2026.

**Meterless Nightshift: the primary AI provider.** Every agent's model call goes to Nightshift's local
OpenAI-compatible endpoint (`METERLESS_BASE_URL`, default `http://127.0.0.1:8088/v1`) with Nightshift's runtime
token, the `meterless-app: coverage-detective` header and one `meterless-scope` per run, so Nightshift's own
memory cannot bring other runs into a prompt. The NVIDIA key lives in Nightshift's Providers tab, not in this
project. Model ids are pinned to Nemotron (`meterless/auto` is refused); from each answer's `meterless` object
we record the answering model (`routed_model`), the receipt id, the route and the latency, and check the
receipt's Ed25519 signature. All of that is in each model call's trace line and in the UI's "Technical
details". `STRICT_MODEL=1` rejects an answer from a non-Nemotron model. NVIDIA's API directly is only a
fallback when Nightshift is unreachable, and it is labelled as such (`provider_fallback`). One request to
check the setup: `.venv/bin/python scripts/nightshift_probe.py --schema`.

**NVIDIA Nemotron: the models.** **Nemotron 3.5 Super** (`nvidia/nemotron-3.5-super-vl-120b-a12b`) runs the
investigator (tool calling, median 1.3 s a call), the diagnostician (strict JSON schema, median 2.5 s), the
advisor (5.5 s), the fix planner (1.4 s) and the letter writer. **Nemotron 3 Ultra**
(`nvidia/nemotron-3-ultra-550b-a55b`) with thinking runs the critic (median 39 s), where careful reasoning
matters most. Bursts of server errors are retried (2 s, 6 s), then the other model answers; both are shown in
the trace. A whole run took 49-180 s live (the examples below); a replay takes about 2 s. (Medians from the
recorded example runs.)

**Cursor:** optional for this track; Coverage Detective was built with Claude Code.

## Examples: inputs, outputs and traces

[`input_examples/`](input_examples/) has nine `POST /run` bodies and [`output_examples/`](output_examples/) the
answers they produced on `run.py --sample` (saved by `scripts/save_output_examples.py`, with each run's trace).
The seven recorded examples, with full traces:

| Example | What it shows | Agents' headline | Rounds | Live run |
|---|---|---|---:|---:|
| [`great_langdale`](examples/great_langdale/) | hills block every operator (terrain 0.70); evidence letters to the operator, council and MP | Hills between you and the nearest masts are the likely cause. | 1 | 81 s |
| [`near_rhayader`](examples/near_rhayader/) | **the critic loop**: it asks for a check, a second round runs, a weak belief is downgraded | Terrain blocking EE and Three signals is the likely cause. | 2 | 180 s |
| [`central_london`](examples/central_london/) | fast measured service: "no strong cause" (good data, nothing above 0.3) | No strong cause was found; the service measures well here. | 1 | 49 s |
| [`sutton_st_edmund`](examples/sutton_st_edmund/) | flat Fens: distance, not terrain | Distance to the nearest mast is the likely cause. | 1 | 72 s |
| [`remote_powys`](examples/remote_powys/) | **insufficient data**: no tests and no site within 10 km | Not enough data to say why: there are no measured speeds at this point or within 1 km and no mast sites in our data within 10 km, so operators could not be identified. | 1 | 54 s |
| [`zone_ousby`](examples/zone_ousby/) | gap zone with no measurements: candidate sites, fix plan reviewed by the critic | No strong cause was found because there are no recent measurements. | 1 | 96 s |
| [`opportunity_windermere`](examples/opportunity_windermere/) | improvement opportunity: capacity upgrade, opportunity wording checked | Busy-network capacity strain is the likely cause. | 1 | 92 s |
| [`summary_uk`](examples/summary_uk/) | priority summary of the sample area (gap and opportunity sections) |  |  |  |
| [`summary_westmorland`](examples/summary_westmorland/) | priority summary for one council |  |  |  |

Each folder has the result, the Meterless trace, the evidence with SQL, the World Model and the recorded
replies. They were recorded live on sample data on 27 Oct 2026, so `run.py --sample` replays them exactly.

## Logs and traces

- `logs/run_<run_id>.jsonl`: one line per step of every run, also printed to stdout when the run ends:
  `timestamp, run_id, agent_name, action, target_agent, input_summary, output_summary, provider,
  model_requested, model_answered, receipt_id, confidence, retry_count, status`. It shows the named agents and
  their handoffs through the case file (`target_agent`), the critic's requests and the extra round, guard
  interventions, every Zetaris query (tables, rows, ms), H-MEM and World Model reads and writes, and each
  model call's provider and answering model. Example: [examples/near_rhayader/agent_trace.jsonl](examples/near_rhayader/agent_trace.jsonl).
- `logs/zetaris_queries.jsonl`: every Zetaris query (SQL, time, rows, error).
- `data/runs/<run_id>.json` (progress) and `.full.json` (result, audit, evidence, notes, World Model);
  `GET /api/runs/<run_id>?detail=1` serves the same.

`logs/` and `data/runs/` are created when the app runs (both gitignored).

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

The data itself (about 5 GB of downloads, a few hours to build) is described in [docs/technical.md](docs/technical.md).

**Docker, full mode**: the container reaches Zetaris and Nightshift on the host through `host.docker.internal`
(on Linux add `--add-host=host.docker.internal:host-gateway`; Nightshift must listen on an address the
container can reach). It reads your `.env` (mounted read-only; `-e` values win) and needs the built data mounted:

```bash
docker run --rm -p 8000:8000 --add-host=host.docker.internal:host-gateway -v "$PWD/.env:/app/.env:ro" \
  -e SAMPLE_MODE=false -e ZETARIS_URL=http://host.docker.internal:3000 \
  -e METERLESS_BASE_URL=http://host.docker.internal:8088/v1 -v "$PWD/data:/app/data" coverage-detective
```

## Repository layout

| Folder | What it holds |
|---|---|
| `coverage_detective/` | the Python package: evidence API, agents (prompts, guard, loop, rules for sample mode), Zetaris and sample-mode clients, planning (zones, scores, siting), FastAPI app |
| `web/` | the three pages (plain HTML/JS, MapLibre + deck.gl) |
| `meterless_service/` | our small Node HTTP service over the Meterless engines (the agents' memory) |
| `vendor/meterless/` | the Meterless H-MEM and World Model reference engines, vendored unchanged (Apache 2.0) |
| `scripts/` | data downloads and builds, Zetaris registration, the data report, sample extracts, recording examples, the Nightshift probe, the Meterless tests, screenshots |
| `examples/` | the 7 recorded runs and 2 summaries replayed by sample mode |
| `input_examples/`, `output_examples/` | `POST /run` requests and the answers they produced |
| `sample_data/` | small committed extracts of every Zetaris source, DEM crops and saved live responses |
| `data/` | the data manifest, reference tables and data notes (built data, runs and memory stores are gitignored) |
| `docs/` | technical reference, data report, submission text, screenshots |
| `tests/` | offline tests (no Zetaris, network or key) |
| `zetaris/` | the Docker Compose overlay that mounts our data into Zetaris |

## Data sources and licences

Ookla open data (CC BY-NC-SA 4.0), OpenCelliD (CC BY-SA 4.0), OpenStreetMap (ODbL), Ofcom Wireless Telegraphy
Register and Connected Nations, ONS boundaries (OGL), WorldPop (CC BY 4.0), Copernicus DEM GLO-30, and live
postcodes.io, Open-Meteo, Met Office warnings and Environment Agency floods. Because Ookla's licence is
non-commercial, the data and everything derived from it are **for non-commercial use**; the attribution footer
on every page must be kept. Details and pre-existing components: [THIRD_PARTY.md](THIRD_PARTY.md). Our code: [MIT](LICENSE).

## Limitations and honesty

- **Causes are inferred, not confirmed.** Operators' network data is not public.
- **No building-level data.** Everything is at an exact point or a ~5 km² hexagon; indoor signal, walls and
  local clutter are not modelled.
- **Ookla has no time-of-day data**: quarterly aggregates only, so congestion can only be inferred ("capacity
  strain"), and many places have few or no tests.
- **Crowd-sourced mast sites are incomplete and approximate**, so "no site found" is weak evidence; operators
  are often inferred, not confirmed.
- **Ofcom figures are area-level** (constituency / council) predictions, not coverage at your address; use
  Ofcom's own [checker](https://www.ofcom.org.uk/mobile-coverage-checker) for that. We have not compared our
  answers with the checker's.
- **Line of sight is an estimate**: a surface model (trees and buildings partly included), antenna heights
  mostly assumed (25 m), no radio propagation model. "Blocked" means weaker, not none.
- **The committed example replies were recorded through NVIDIA's API directly** (27 Oct 2026, before the
  Nightshift integration), so their traces show the Nemotron model that answered but no Nightshift receipt.
  Re-recording them through Nightshift is one command: `scripts/record_examples.py`.
- **Nightshift's interface** (headers, token file, the `meterless` receipt object) was implemented from two
  working Nightshift 1.2.3 integrations rather than its documentation; `scripts/nightshift_probe.py` checks it.
- **Sample mode is a slice**: rankings are within the sample only (e.g. "#4 of 4"); away from the recorded
  examples the agents' replies are fixed rules, not a model; Explore shows cell details but not the UK-wide map
  layers (15 MB, built from the full data).
- **A contact address can go stale.** The table records where each address came from and when it was checked;
  run `scripts/check_outreach.py --to all` before a release. A long letter does not fit in a `mailto` link, so a
  cut message keeps the letter's start and the sign-off and leaves the full text to Copy.
- Mobile networks only; home broadband is out of scope.

**What we would do next:** compare our answers with Ofcom's checker and with measured drive tests; deprivation
and rural-urban "need" in the council score; power cuts as an incident cause; flood warnings for Scotland, Wales
and Northern Ireland; operator-confirmed site data or Ofcom's postcode API if access is granted; a radio
propagation model for candidate sites.

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
More: [docs/technical.md](docs/technical.md); submission text: [docs/SUBMISSION.md](docs/SUBMISSION.md).
