# Coverage Detective: submission text

Text for the HackOS submission form. Every claim here is true of the code in this repository; the README has
the details and the commands to check them.

## Project name

Coverage Detective

## Tagline

AI agents that explain why your mobile signal is bad, and show councils where to act, from real public data,
with every claim traced to its evidence and SQL.

## Track

Track 2: The Agent That Can Explain Why

## Short description

Type a UK postcode or click a spot: a team of agents (investigator, diagnostician, critic, advisor) queries
public mobile, mast, terrain and Ofcom data through Zetaris, argues over the evidence in a shared Meterless
case file, and returns the likely causes with confidence, what was ruled out, the best network and what to do.
For councils, it ranks poorly served zones and proposes fixes with candidate mast sites.

## Long description

Many people in the UK still have poor mobile signal where they live: our data finds 677 poorly served gap
zones, home to about 265,000 people. Nobody tells them why. Operators' network data is private, Ofcom's checker
predicts coverage but does not explain it, and councils deciding where to push for new masts have spreadsheets,
not reasons.

Coverage Detective answers "why is my signal bad here?" for an exact point, and "where are the gaps, why, and
what would fix them?" for a council area. It uses only public data: Ookla speed tests (four quarters),
crowd-sourced and mapped mast sites (OpenCelliD, OpenStreetMap) checked against Ofcom's licence register,
Ofcom Connected Nations, the Copernicus 30 m elevation model for line of sight from the exact point to each
operator's nearest site, population, and live weather, Met Office warnings and Environment Agency floods.

The agents work in a loop of up to three rounds. The investigator chooses tools that query Zetaris; the
diagnostician ranks causes from a fixed set (distance to mast, terrain, single operator, capacity strain,
weather, active incident, insufficient data) with confidence and evidence ids; the critic approves, asks for
specific checks (which starts another round), downgrades or rejects, with reasons; the advisor writes the
answer for a resident, council officer or operator, and the letter writer drafts an email to the council or
network. For zones, a fix planner proposes a fix from candidate sites and the critic reviews it. Agents never
message each other: each reads and writes the run's case file in Meterless H-MEM. A code guard checks every
agent's output (citations must exist, every number must match the cited evidence, weak-only beliefs are capped
at 0.5, earlier diagnoses are context only) and records every intervention.

Everything is visible: "How the agents decided" shows each round, the critic's challenges and each belief's
confidence history; every claim opens to its evidence item and the Zetaris SQL behind it; each run writes a
JSONL trace (agents, handoffs, Zetaris queries, memory reads and writes, guard interventions, the model that
answered each call). "Not enough data to say why" is a valid answer, and "no site in our data" is never shown
as "no coverage". `POST /run` gives the same answer as JSON for scripts. Sample mode runs the whole pipeline
with no keys or services (recorded model replies for ten examples, fixed rules elsewhere, clearly labelled).

## Sponsor technology

**Zetaris (the evidence layer).** Every dataset is its own Zetaris Lightning source: 15 Parquet sources
(cells and population, Ookla speeds, history and tiles, mast sites and per-cell mast statistics, Ofcom
register points, Ofcom area and nation figures, terrain, line of sight, gap zones and their cells, improvement
opportunities and their cells) plus a live REST source for Environment Agency flood warnings
(`CD_LIVE_EA_FLOODS`). The agents' tools query them with federated SQL; one query per place joins cells, Ookla
speeds and history, masts, Ofcom, terrain and line of sight. Every query is logged with the evidence it
produced, so each claim on screen opens to its SQL, and each run's trace lists the queries with their row counts
and times. Setup is scripted (`scripts/zetaris/compose.sh`, `scripts/zetaris/register_sources.py`).

**Meterless Engines: H-MEM and World Model.** We run the open-source H-MEM and World Model reference engines
(vendored unchanged, Apache 2.0) behind our small Node service. H-MEM holds each run's case file: evidence,
beliefs, critiques and notes, with every belief revision a trust-ledger update (who, old and new confidence,
why). It is the agents' only channel to each other, and its ledger is the "How the agents decided" view;
earlier diagnoses nearby are recalled from it as context. The World Model holds places and zones, each
operator's nearest site with the line-of-sight result, warnings, candidate sites and events; it powers the
World Model graph in the Evidence tab and the fix view's candidate sites. Tests: `scripts/test_meterless.sh`
runs the H-MEM suite (10/10), the World Model suite (10/10), our service over both (4/4) and the Python client
(2/2): 26/26 passed.

**Meterless Nightshift (the first-choice AI provider in the code).** Every agent's model call goes to Nightshift's local
OpenAI-compatible endpoint with Nightshift's runtime token, the `meterless-app: coverage-detective` header and
a fresh `meterless-scope` per run. The NVIDIA key is connected in Nightshift's Providers tab, not in our code.
Model ids are pinned to Nemotron (`meterless/auto` is refused), and from each answer we record the model that
actually answered (`routed_model`), the receipt id, the route and the latency, and verify the receipt's Ed25519
signature. These appear on each model call in the trace and in the UI's "Technical details"; an optional strict
mode rejects any non-Nemotron answer. NVIDIA's API directly is used only if Nightshift is unreachable, and is
labelled as a fallback. Nightshift runs on macOS, Windows and ARM64 only and our machine is Linux x86_64, so our
runs used NVIDIA's hosted API directly, labelled as such on every call; the Meterless organisers confirmed this
is acceptable for the provider requirement, since the H-MEM and World Model engines are active in the project.

**NVIDIA Nemotron (the models).** Nemotron 3.5 Super runs the investigator (tool calling), diagnostician,
advisor, letter writer and fix planner, with strict JSON-schema output; Nemotron 3 Ultra with thinking runs the
critic, where careful reasoning matters most. Each request's model is shown in the trace. In the recorded runs,
median call times were 1.3 s (investigator), 2.5 s (diagnostician), 5.5 s (advisor) and 39 s (critic); a whole
run took 49-180 s.

**Cursor.** Optional for this track; Coverage Detective was built with Claude Code.

## What is unique

- **It explains, and shows its working.** Causes come with confidence, evidence, what was ruled out and the
  limits of the data; every number on screen traces to a SQL query over public data.
- **Agents that disagree.** The critic challenges beliefs, asks for specific checks and forces extra rounds;
  confidence changes are recorded with reasons in a trust ledger, so the decision path can be replayed.
- **A code guard, not just prompts.** Numbers must match the evidence; unsupported sentences are rewritten once,
  then removed, and every correction is shown.
- **Exact-point physics.** Line of sight from the user's exact point to each operator's nearest site over a 30 m
  elevation model, not a hexagon average.
- **Honest with thin data.** "Not enough data to say why" is an answer; crowd-sourced gaps are labelled weak.
- **From diagnosis to action.** A council view of ranked gaps and upgrade opportunities with candidate sites,
  and an email to the council or network with the evidence attached.

## Pre-existing components

- **Meterless H-MEM and World Model reference engines** (`vendor/meterless/`): from github.com/Meterless/Meterless
  at commit `1742dec`, Apache 2.0, vendored unchanged on 26 Oct 2026.
- **Zetaris platform bundle** (local Docker stack, provided by the sponsor; not in this repository).
- **Meterless Nightshift** (local app, provided by the sponsor) and **NVIDIA Nemotron** models (API).
- **Open-source libraries**: Python (FastAPI, pandas, numpy, scipy, rasterio, pyosmium, DuckDB, h3, geopandas,
  shapely, httpx, pydantic, cryptography), Node (tsx), and the web pages' MapLibre GL, deck.gl and h3-js, with
  OpenFreeMap tiles. Licences in THIRD_PARTY.md.
- **Public datasets**, all downloaded during the hackathon (23-25 Oct 2026, dates in `data/manifest.json`): Ookla
  open data, OpenCelliD, OpenStreetMap (Geofabrik), Ofcom Wireless Telegraphy Register and Connected Nations
  (July 2025 release), ONS boundaries, WorldPop, Copernicus DEM GLO-30; live postcodes.io, Open-Meteo, Met Office
  and Environment Agency feeds.
- **Our code** was written during the hackathon: the repository's first commit is 25 Oct 2026.
