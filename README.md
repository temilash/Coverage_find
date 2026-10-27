# Coverage Detective

**AI agents that explain why your mobile signal is bad, and show councils where to act, from real public data,
with every claim traced to its evidence and SQL.**

Open Agent Hackathon, **Track 2: The Agent That Can Explain Why**.

![My signal: the agents' diagnosis for Great Langdale](docs/screenshots/hero_my_signal.png)

## The problem

Many people in the UK still have poor mobile signal where they live (our data finds 677 poorly served gap
zones, home to about 265,000 people), but nobody tells them **why**: is it the hills, the distance to the mast,
a single network, a busy cell, or today's storm? Operators' network data is private, Ofcom's checker predicts
coverage but does not explain it, and councils deciding where to push for new masts have spreadsheets, not
reasons. Coverage Detective gives **residents** the likely causes, the best network and a ready email to their
council or network, and gives **councils** ranked gap zones and upgrade opportunities with a proposed fix.

## Try it in 30 seconds (sample mode: no keys, no Zetaris, no Nightshift)

```bash
git clone https://github.com/temilash/Coverage_find.git && cd Coverage_find
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
(cd meterless_service && npm install)     # Node 18+: the agents' memory service
.venv/bin/python run.py --sample          # then open http://localhost:8000/signal
```

Or `docker build -t coverage-detective . && docker run --rm -p 8000:8000 coverage-detective`. Then:

1. **CO9 1TP** (Gosfield, Essex): search `CO9 1TP` on My signal → **Diagnose with agents** → **Send this
   diagnosis** → Braintree Council (or a network) → **Write the email**: the drafted email with its address.
2. **Great Langdale**: `/signal?lat=54.445&lon=-3.105` → Diagnose. Hills block every network; open
   **Evidence** to see each item and the SQL behind it.
3. **Near Rhayader**: `/signal?lat=52.30&lon=-3.51` → Diagnose → **How the agents decided**: the critic
   challenges a belief, asks for a check, and a second round runs.
4. **Council view** → `/council?zone=Z00072` (**Ousby**) → Diagnose zone: the recommended fix and **Today /
   With candidate 1** on the map.

From a script: `curl -s -X POST localhost:8000/run -H 'content-type: application/json' -d @input_examples/example_1.json`
([all the requests and answers](docs/usage.md)). Sample mode runs the real pipeline over small committed
extracts; the model's replies are recorded for the examples, fixed rules (labelled) elsewhere nearby, and other
places are refused with the areas it covers ([exactly what it does](docs/usage.md#what-sample-mode-does-exactly)).

## Examples

| Example | What it shows | Agents' headline | Rounds | Live run |
|---|---|---|---:|---:|
| [`co9_1tp`](examples/co9_1tp/) | **the first sample**: slow measured service from only 3 tests, so no cause is strong; the critic rejects "single operator"; recorded emails to Braintree Council and all four networks | The service measures poorly here, but no single cause reaches a strong level. | 2 | 416 s |
| [`great_langdale`](examples/great_langdale/) | hills block every operator (terrain 0.70); evidence letters to the operator, council and MP | Hills between you and the nearest masts are the likely cause. | 1 | 81 s |
| [`near_rhayader`](examples/near_rhayader/) | **the critic loop**: it asks for a check, a second round runs, a weak belief is downgraded | Terrain blocking EE and Three signals is the likely cause. | 2 | 180 s |
| [`central_london`](examples/central_london/) | fast measured service: "no strong cause" (good data, nothing above 0.3) | No strong cause was found; the service measures well here. | 1 | 49 s |
| [`sutton_st_edmund`](examples/sutton_st_edmund/) | flat Fens: distance, not terrain | Distance to the nearest mast is the likely cause. | 1 | 72 s |
| [`remote_powys`](examples/remote_powys/) | **insufficient data**: no tests and no site within 10 km | Not enough data to say why: there are no measured speeds at this point or within 1 km and no mast sites in our data within 10 km, so operators could not be identified. | 1 | 54 s |
| [`zone_ousby`](examples/zone_ousby/) | gap zone with no measurements: candidate sites, fix plan reviewed by the critic | No strong cause was found because there are no recent measurements. | 1 | 96 s |
| [`opportunity_windermere`](examples/opportunity_windermere/) | improvement opportunity: capacity upgrade, opportunity wording checked | Busy-network capacity strain is the likely cause. | 1 | 92 s |
| [`zone_broadstone`](examples/zone_broadstone/) | gap zone: hills block every nearby mast (terrain 0.65), candidate high-ground sites, fix reviewed by the critic | Terrain blocking the line to every nearby mast is the likely cause. | 1 | 256 s |
| [`opportunity_ludlow`](examples/opportunity_ludlow/) | improvement opportunity over 108 cells and five councils, opportunity wording checked; a close call: the headline names capacity strain (0.40), ranked just below single operator (0.45) | No single cause dominates this zone; a capacity strain across many people is the most likely contributor. | 1 | 120 s |
| [`summary_uk`](examples/summary_uk/), [`summary_westmorland`](examples/summary_westmorland/) | priority summaries of the sample area and of one council |  |  |  |

Each folder has the result, the Meterless trace, the evidence with SQL, the World Model and the recorded model
replies; `examples/near_rhayader/agent_trace.jsonl` is a run's step-by-step trace.

## How it works

```
 public data ──► Zetaris ──► evidence API ──► agents ◄──► Meterless ──► pages, POST /run
 (Ookla, masts,   (one        (findings with   (investigate,   (H-MEM case      (My signal,
  Ofcom, DEM,      source per   strength, data   diagnose,       file + World     Council view,
  live APIs)       dataset)     period and SQL)  critique, act)  Model)           Explore)
                                      ▲                │  ▲
                                      └── code guard ──┘  └── Nemotron (Nightshift, or NVIDIA's API)
```

Up to three rounds of **investigate → diagnose → critique**: the investigator queries the data, the
diagnostician ranks causes from a fixed set with confidence and evidence ids, and the critic approves, asks for
specific checks (another round), downgrades or rejects. Then the advisor writes the answer (for a zone, the fix
planner proposes a fix and the critic reviews it). Agents never message each other; they share a case file in
Meterless. A code guard checks every output: citations must exist, every number must match the cited evidence,
weak-only beliefs are capped. More: [docs/agents.md](docs/agents.md).

## Sponsor technology

- **Zetaris** is the evidence layer: every dataset is its own Lightning source (15 Parquet sources plus a live
  REST source for flood warnings), the agents' tools query them with federated SQL, and every claim on screen
  opens to its SQL.
- **Meterless Engines**: the **H-MEM** engine is the agents' shared case file and trust ledger (the "How the
  agents decided" view); the **World Model** holds places, sites per operator with line of sight, candidate
  sites and events. `scripts/test_meterless.sh`: 26/26 tests pass.
- **Meterless Nightshift** is the code's first-choice AI provider (answering model, receipt id and signature
  recorded per call), with NVIDIA's API directly as a labelled fallback. Nightshift does not run on our Linux
  x86_64 machine, so our runs used NVIDIA's hosted API, labelled on every call; the Meterless organisers
  confirmed this is acceptable.
- **NVIDIA Nemotron**: Nemotron 3.5 Super runs the investigator, diagnostician, advisor, letter writer and fix
  planner; Nemotron 3 Ultra with thinking runs the critic.
- **Cursor**: optional for this track; Coverage Detective was built with Claude Code.

Details, timings and test results: [docs/sponsors.md](docs/sponsors.md).

## Limitations

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
- **The example replies were recorded through NVIDIA's hosted API directly** (27 Oct 2026), not Nightshift,
  which does not run on our Linux x86_64 machine; their traces show the Nemotron model that answered but no
  Nightshift receipt.
- **The Nightshift integration is untested against a live Nightshift**: its interface (headers, token file, the
  `meterless` receipt object) was implemented from two working Nightshift 1.2.3 integrations and is covered by
  mocked tests; `scripts/nightshift_probe.py` checks it on a machine that runs Nightshift.
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

## Documentation

- [docs/setup.md](docs/setup.md): full mode, Zetaris, Nightshift, Docker, settings, the data build
- [docs/usage.md](docs/usage.md): the pages, `POST /run` with curl, sample inputs and outputs, emails and letters
- [docs/agents.md](docs/agents.md): the agent loop, roles, code guard, traces and logs
- [docs/sponsors.md](docs/sponsors.md): Zetaris, the Meterless engines and their tests, Nightshift, NVIDIA, Cursor
- [docs/data.md](docs/data.md): sources, licences, freshness, repository layout ([data report](docs/data_report.md))
- [docs/SUBMISSION.md](docs/SUBMISSION.md): the submission text; [THIRD_PARTY.md](THIRD_PARTY.md); our code: [MIT](LICENSE)
