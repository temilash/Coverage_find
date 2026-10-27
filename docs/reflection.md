# Reflection: Zetaris and Meterless

## Zetaris: the unified semantic layer

**What we built on it.** Sixteen Zetaris sources (15 Parquet Lightning sources and one live REST source), each
registered by one short function in `scripts/zetaris/register_sources.py`, and one federated query per place
(`cell_sql()` in `coverage_detective/data/queries.py`) that joins cells, Ookla speeds and history, mast
statistics, Ofcom area figures, terrain and line of sight. The same SQL feeds the explorer's cell panel, the
evidence API and the agents' tools, and every query is logged with the evidence it produced, so a claim on screen
opens to the exact SQL behind it. The view in [usl.md](usl.md) is the core of that query.

**Time saved versus hand-coded joins (an estimate, not a measurement).** We did not build the pandas version, so
this is a judgement from the work we did do. In pandas, each consumer (explorer, evidence API, agent tools,
council view) would need its own loading of 5-8 Parquet files, its own merges and filters, and its own way to
show where a number came from. With Zetaris that is one SQL string per question, written once and reused, and the
"show the SQL" provenance comes free with the query log. Our estimate is that it saved about a day of plumbing
over the three days, most of it in provenance (the evidence items' SQL and the per-run query trace), which we
would otherwise have built by hand. Against that, Zetaris cost us time too, all recorded in `CLAUDE.md`: local
files are readable only under one mounted path; a plain `docker compose up` drops our data mount (hence
`scripts/zetaris/compose.sh`); every value comes back as a string and rows lose their SELECT column order, so the
client converts types; tokens expire after 15 minutes; parameterised REST endpoints fail at registration because
`CREATE` calls the endpoint with a literal `${...}`; and, on 27 Oct, `CREATE VIEW` appeared to succeed but the
view could not be queried afterwards (see [usl.md](usl.md)).

**A product manager's killer requirement.** *Every object the SQL editor accepts must be queryable through the
query API in the next statement, or the statement must fail with an error that says why.* Our `CREATE VIEW`
returned success, then both the query API and the SQL editor answered `Not exist`; that silent gap is the worst
failure for agents, which treat a successful statement as a fact. The same principle covers schema drift: when
a registered Parquet source's columns change, registration (or the first query) should fail with the old and new
schema, never return a quietly different result.

## Meterless: H-MEM and the World Model

**H-MEM as the critic's trust ledger.** Each run's case file lives in H-MEM: the investigator's evidence items
(with their SQL), the diagnostician's beliefs, the critic's critiques, and notes for every model call, tool call
and guard intervention. A belief's confidence only changes through a ledger update that records who changed it,
the old and new values and the reason, and the code refuses a change without a reason. The critic reads each
belief with that history, so it judges how a belief got its confidence, not only its current value; when it
downgrades or rejects, the change is written back as a ledger revision with its reason, and the next round's
diagnostician sees it. "How the agents decided" in the UI is built from this ledger, and so is each run's JSONL
trace. Earlier diagnoses nearby are recalled from H-MEM as context for new runs; the critic is deliberately not
shown them, because in testing it treated them as evidence.

**What the World Model holds.** The coordinator writes the spatial state of each run into the World Model: the
place (or zone) and its local authority, each operator's nearest site with the line-of-sight result
(clear / marginal / blocked, obstruction height, distance, the evidence id), whether the operator is confirmed by
Ofcom's register or inferred, active weather warnings, and for zones the candidate sites with the people each
would give a clear line. To be precise about timing: the fix planner works from the candidate-site evidence in
H-MEM, and the candidate sites are written into the World Model after it has chosen, together with a
`fix_plan_proposed` event. The World Model is the durable, connected record of that state (shown as the graph in
the Evidence tab), not an input the planner queries during its run.

**Why this beats passing JSON between agents.** With JSON handoffs, each agent sees only what the previous one
chose to pass on, a confidence change has no author or reason, and nothing survives the run. With the shared
case file: every agent reads the same evidence, so the critic can check a citation against the item itself; the
guard can verify every number against the evidence it cites; a revision without a reason is impossible; a retry
or a second round starts from what is already known instead of a re-sent blob; and the whole decision path can be
read back afterwards from the ledger. The cost is a
small service to run (`meterless_service/`) and an in-memory fallback for when it is down.
