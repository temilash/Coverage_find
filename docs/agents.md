# Agents

The agent loop, the agents' roles, the code guard, and the traces and logs every run leaves.

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

## Logs and traces

- `logs/run_<run_id>.jsonl`: one line per step of every run, also printed to stdout when the run ends:
  `timestamp, run_id, agent_name, action, target_agent, input_summary, output_summary, provider,
  model_requested, model_answered, receipt_id, confidence, retry_count, status`. It shows the named agents and
  their handoffs through the case file (`target_agent`), the critic's requests and the extra round, guard
  interventions, every Zetaris query (tables, rows, ms), H-MEM and World Model reads and writes, and each
  model call's provider and answering model. Example: [examples/near_rhayader/agent_trace.jsonl](../examples/near_rhayader/agent_trace.jsonl).
- `logs/zetaris_queries.jsonl`: every Zetaris query (SQL, time, rows, error).
- `data/runs/<run_id>.json` (progress) and `.full.json` (result, audit, evidence, notes, World Model);
  `GET /api/runs/<run_id>?detail=1` serves the same.

`logs/` and `data/runs/` are created when the app runs (both gitignored).
