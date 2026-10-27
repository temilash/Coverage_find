# Sponsor technology

How Zetaris, the Meterless engines, Meterless Nightshift, NVIDIA Nemotron and Cursor are used.

## Sponsor tech

**Zetaris: the evidence layer.** Every dataset is its own Zetaris Lightning source (15 Parquet sources such as
`CD_CELLS`, `CD_OOKLA_MOBILE`, `CD_MASTS_SITES`, `CD_OFCOM_AREA`, `CD_LOS`, `CD_GAP_ZONES`, plus the live REST
source `CD_LIVE_EA_FLOODS`), and the agents' tools query them with federated SQL: one query per place joins
cells, Ookla speeds and history, masts, Ofcom, terrain and line of sight. Every query is logged with the
evidence it produced, so each claim on screen opens to its SQL. Row counts, freshness and query times
(71-576 ms per source, 366-467 ms for the cross-source query per place): [docs/data_report.md](data_report.md).
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

**Meterless Nightshift: the first-choice AI provider in the code.** Every agent's model call goes to Nightshift's local
OpenAI-compatible endpoint (`METERLESS_BASE_URL`, default `http://127.0.0.1:8088/v1`) with Nightshift's runtime
token, the `meterless-app: coverage-detective` header and one `meterless-scope` per run, so Nightshift's own
memory cannot bring other runs into a prompt. The NVIDIA key lives in Nightshift's Providers tab, not in this
project. Model ids are pinned to Nemotron (`meterless/auto` is refused); from each answer's `meterless` object
we record the answering model (`routed_model`), the receipt id, the route and the latency, and check the
receipt's Ed25519 signature. All of that is in each model call's trace line and in the UI's "Technical
details". `STRICT_MODEL=1` rejects an answer from a non-Nemotron model. NVIDIA's API directly is only a
fallback when Nightshift is unreachable, and it is labelled as such (`provider_fallback`). One request to
check the setup: `.venv/bin/python scripts/nightshift_probe.py --schema`.

**How our runs were made.** Nightshift runs on macOS, Windows and ARM64 (DGX Spark) only; our machine is
Linux x86_64, so it could not run Nightshift. With no Nightshift token the client goes straight to NVIDIA's hosted
API (`LLM_API_KEY`), and every model call records that (`provider: nvidia_direct`, with the reason as a
`provider_fallback`). The Meterless organisers confirmed that the standard NVIDIA hosted API is acceptable for
the provider requirement, since the H-MEM and World Model engines are active in the project. All recorded
examples were answered by NVIDIA Nemotron through that API.

**NVIDIA Nemotron: the models.** **Nemotron 3.5 Super** (`nvidia/nemotron-3.5-super-vl-120b-a12b`) runs the
investigator (tool calling, median 1.3 s a call), the diagnostician (strict JSON schema, median 2.5 s), the
advisor (5.5 s), the fix planner (1.4 s) and the letter writer. **Nemotron 3 Ultra**
(`nvidia/nemotron-3-ultra-550b-a55b`) with thinking runs the critic (median 39 s), where careful reasoning
matters most. Bursts of server errors are retried (2 s, 6 s), then the other model answers; both are shown in
the trace. A whole run took 49-180 s live (the examples below); a replay takes about 2 s. (Medians from the
recorded example runs.)

**Cursor:** optional for this track; Coverage Detective was built with Claude Code.

## Meterless service (agent memory)

`meterless_service/server.ts` (Node 18+, `cd meterless_service && npm install`) is a small
HTTP service on port 8100 over the two vendored engines; `python run.py` starts it next to
the web app and waits for its health check. It persists to `data/meterless/` (gitignored).

- H-MEM: runs, evidence items (stable ids per run, idempotent), beliefs (cause + confidence),
  belief revisions as trust-ledger updates (who, old → new confidence, reason), critiques,
  notes (guard interventions, tool calls), the ordered audit trail of a run, and past
  diagnoses near a point (within ~10 km).
- World Model: entities (place, zone, site, operator, warning), relationships (scoped by an
  operator context, e.g. place → nearest_site{EE} → site), events (diagnosis_completed,
  fix_plan_proposed).
- Python client: `coverage_detective/memory/meterless.py` (5 s timeout, one retry). If the
  service is down it logs loudly and uses an in-memory store with the same interface.

Reads are recorded too: the client notes which agent read what from the case file (set per agent by the
coordinator), for the run's trace.

Tests, all in one command:

```bash
scripts/test_meterless.sh   # H-MEM engine 10/10, World Model engine 10/10, the service 4/4, the Python client 2/2
```

## Model provider: Meterless Nightshift

`coverage_detective/agents/llm.py`. Every model call goes to Nightshift first:

- `POST {METERLESS_BASE_URL}/chat/completions` (default `http://127.0.0.1:8088/v1`), OpenAI-compatible.
- Headers: `Authorization: Bearer <token>` (from `METERLESS_RUNTIME_TOKEN`, else the file named by
  `METERLESS_RUNTIME_TOKEN_FILE`, Nightshift's `<state dir>/proxy-token`; read at call time, never stored,
  logged or printed), `meterless-app: coverage-detective`, and `meterless-scope: coverage-detective-<run_id>`
  (`METERLESS_FRESH_SCOPE=1`: a new scope per call). Timeout 180 s (a model load can take minutes).
- Models pinned: `LLM_MODEL` (Nemotron 3.5 Super: investigator, diagnostician, advisor, letters, fix planner) and
  `LLM_MODEL_REASONING` (Nemotron 3 Ultra, thinking on: the critic). `meterless/auto` and ids without a `/`
  are refused.
- From the answer's top-level `meterless` object: `routed_model` (the answering model; else the response's
  `model`), `receipt_id`, `winning_route.provider/kind`, `usage.latency_ms`, and `receipt_envelope`, whose
  Ed25519 signature over the body (compact JSON, sorted keys) is checked (`signature_valid`). A missing object
  is recorded as "no Meterless receipt in the answer". Committed traces keep the ids, not the envelope.
- `STRICT_MODEL=1` rejects an answer whose routed model is not Nemotron.
- Nightshift unreachable, 401 ("unauthorized: check METERLESS_RUNTIME_TOKEN") or 5xx after the retries: NVIDIA
  direct answers (`LLM_API_KEY`, `LLM_BASE_URL`), recorded as a `provider_fallback` note with the reason. A
  4xx such as an unregistered app name is not retried elsewhere ("is the meterless-app name registered in
  Nightshift?").

Each `llm_call` note carries provider, model_requested, model_answered and the receipt; the trace, `/run`'s
`models` and the UI's "Technical details" show them. These details were implemented from two working
Nightshift 1.2.3 integrations; check them against your install with
`.venv/bin/python scripts/nightshift_probe.py --schema` (one plain request, then the two structured-output
styles the agents use: strict `json_schema`, and the schema in the prompt).

## Pre-existing components

Not written for this project, used unchanged:

| Component | From | Licence | Used for |
|---|---|---|---|
| Meterless H-MEM reference engine (`vendor/meterless/hmem/`) | [Meterless/Meterless](https://github.com/Meterless/Meterless) `engines/hmem/reference` | Apache 2.0 | agents' shared memory: evidence, beliefs, critiques, trust ledger (audit trail) |
| Meterless World Model reference engine (`vendor/meterless/world-model/`) | same repo, `engines/world-model/reference` | Apache 2.0 | places, zones, sites, operators, warnings, relationships, events |
| Zetaris Lightning (local Docker stack) | Zetaris | sponsor licence | federated SQL over every dataset |
| Meterless Nightshift (local app, not in this repository) | Meterless | sponsor terms | the AI provider: routes the agents' model calls to NVIDIA Nemotron, with receipts |
| NVIDIA Nemotron 3.5 Super and 3 Ultra (API, not in this repository) | NVIDIA | NVIDIA API terms | the agents' models (through Nightshift) |
| Open-source Python and Node libraries (`requirements.txt`, `meterless_service/package.json`) | PyPI / npm | their own licences ([THIRD_PARTY.md](../THIRD_PARTY.md)) | data processing, API, map, service |
| Map and UI libraries: MapLibre GL, deck.gl, h3-js, OpenFreeMap tiles | npm / CDN | BSD-3 / MIT / Apache 2.0 / ODbL | the web pages |

The vendored engines' own tests run with the rest in `scripts/test_meterless.sh` (below). The upstream commit
is recorded in `vendor/meterless/README.md`. Public datasets were all downloaded during the hackathon (23-25 Oct
2026, dates in `data/manifest.json`); the project's first commit is 25 Oct 2026.
