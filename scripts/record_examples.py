"""Record the examples for sample mode: each run uses the sample data (DuckDB extracts, saved live responses)
and the live model, and every agent reply is saved so `python run.py --sample` can replay it with no key.

    python scripts/record_examples.py              # all examples (about 20 minutes)
    python scripts/record_examples.py zone_ousby   # just some
    python scripts/record_examples.py --emails-only co9_1tp   # only the emails, for the run already recorded

Writes examples/<example_id>/ (diagnosis.json, trace.json, evidence.json, world.json, progress.json,
llm_replies.json, agent_trace.jsonl) and examples/index.json (what each example answers, which provider and
models answered, and the hash of the prompts used). Uses its own Meterless store (port 8102,
data/meterless_record/, emptied first) so examples see only each other, in the order below. Needs the
model settings in .env (Meterless Nightshift: METERLESS_RUNTIME_TOKEN or _FILE, LLM_MODEL, LLM_MODEL_REASONING);
it says so after each example if any call was answered other than through Nightshift.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.update({"SAMPLE_MODE": "1", "RECORD_EXAMPLES": "1", "MEMORY_PORT": "8102", "MEMORY_URL": "http://127.0.0.1:8102",
                   "MEMORY_DIR": str(ROOT / "data" / "meterless_record")})
sys.path.insert(0, str(ROOT))

from coverage_detective.agents import coordinator as C  # noqa: E402
from coverage_detective.agents import replay as R  # noqa: E402
from coverage_detective.api import agents as API  # noqa: E402

EXAMPLES = [  # example_id, kind, where, label
    ("co9_1tp", "point", {"lat": 51.935645, "lon": 0.592832}, "CO9 1TP, Gosfield, Essex"),
    ("great_langdale", "point", {"lat": 54.445, "lon": -3.105}, "Great Langdale, Cumbria"),
    ("central_london", "point", {"lat": 51.507211, "lon": -0.128269}, "Central London (WC2N 5DU)"),
    ("near_rhayader", "point", {"lat": 52.30, "lon": -3.51}, "Near Rhayader, Powys"),
    ("sutton_st_edmund", "point", {"lat": 52.700117, "lon": 0.024085}, "Sutton St Edmund, Lincolnshire"),
    ("remote_powys", "point", {"lat": 52.399095, "lon": -3.186185},
     "Remote cell near Bettws, Powys (no tests, no site within 10 km: insufficient data)"),
    ("zone_ousby", "zone", {"zone_id": "Z00072"}, "Ousby gap zone (Westmorland and Furness)"),
    ("opportunity_windermere", "zone", {"zone_id": "O00332"}, "Windermere improvement opportunity"),
    ("zone_broadstone", "zone", {"zone_id": "Z00023"}, "Broadstone gap zone (Shropshire)"),
    ("opportunity_ludlow", "zone", {"zone_id": "O00004"}, "Ludlow improvement opportunity"),
]
LETTERS = {"great_langdale": ["operator", "council", "mp"]}
EMAILS = {"co9_1tp": ["council", "EE", "O2", "Three", "Vodafone"]}  # POST /api/runs/{id}/email, one per recipient
SUMMARIES = [("summary_uk", "uk", "the sample area"), ("summary_westmorland", "la:E06000064", "Westmorland and Furness")]


def export(example_id: str, run_id: str) -> None:
    full = json.loads((C.RUNS_DIR / f"{run_id}.full.json").read_text())
    out = R.EXAMPLES / example_id
    out.mkdir(parents=True, exist_ok=True)
    dump = lambda name, obj: (out / f"{name}.json").write_text(json.dumps(obj, indent=1, default=str))  # noqa: E731
    dump("diagnosis", full["result"])
    dump("trace", {k: full[k] for k in ("audit", "notes", "critiques")})
    dump("evidence", full["evidence"])
    dump("world", full["world"])
    shutil.copy(C.RUNS_DIR / f"{run_id}.json", out / "progress.json")
    trace = C.trace_log.LOGS / f"{run_id}.jsonl"
    if trace.exists():
        shutil.copy(trace, out / "agent_trace.jsonl")


def providers(run_id: str) -> dict:
    """{provider: calls} and the models that answered, from the run's llm_call notes."""
    notes = json.loads((C.RUNS_DIR / f"{run_id}.full.json").read_text())["notes"]
    calls = [n for n in notes if n.get("type") == "llm_call"]
    by = {}
    for n in calls:
        by[n.get("provider") or "unknown"] = by.get(n.get("provider") or "unknown", 0) + 1
    return {"providers": by, "models_answered": sorted({n.get("model_answered") or n.get("model") for n in calls})}


def record_emails(names: list[str]) -> None:
    """Re-record only the emails of examples whose run is already recorded (same run, same Meterless store)."""
    for example_id in names:
        run_id = R.example(example_id)["run_id"]
        path = R.EXAMPLES / example_id / "llm_replies.json"
        data = json.loads(path.read_text())
        data["replies"] = {k: v for k, v in data["replies"].items() if not k.startswith("letter_writer:email ")}
        path.write_text(json.dumps(data, indent=1))
        for to in EMAILS.get(example_id, []):
            mail = API.email(run_id, API.EmailRequest(to=to))
            print(f"{example_id}: email to {mail['contact']['name']}: {mail['subject']} | guard: {mail['guard']}")


def main() -> None:
    from run import start_memory
    from coverage_detective.agents.llm import llm

    if llm.problem:
        sys.exit(f"cannot record: {llm.problem}")
    args = [a for a in sys.argv[1:] if a != "--emails-only"]
    if "--emails-only" in sys.argv:
        start_memory()
        return record_emails(args or list(EMAILS))
    names = args or [e[0] for e in EXAMPLES] + [s[0] for s in SUMMARIES]
    if not args:
        shutil.rmtree(ROOT / "data" / "meterless_record", ignore_errors=True)
    start_memory()
    index = {e["example_id"]: e for e in R.index()}
    for example_id, kind, where, label in EXAMPLES:
        if example_id not in names:
            continue
        (R.EXAMPLES / example_id / "llm_replies.json").unlink(missing_ok=True)
        sess = R.Session("record", example_id)
        t = time.time()
        r = (C.diagnose_point(**where, replay=sess) if kind == "point" else C.diagnose_zone(where["zone_id"], replay=sess))
        export(example_id, r["run_id"])
        for recipient in LETTERS.get(example_id, []):
            API.letter(r["run_id"], API.LetterRequest(recipient=recipient))
        for to in EMAILS.get(example_id, []):
            mail = API.email(r["run_id"], API.EmailRequest(to=to))
            print(f"   email to {mail['contact']['name']}: {mail['subject']}")
        used = providers(r["run_id"])
        index[example_id] = {"example_id": example_id, "kind": kind, "label": label, **where, "run_id": r["run_id"],
                             "status": r["status"], "headline": r["headline"], "elapsed_s": r["elapsed_s"],
                             "letters": LETTERS.get(example_id, []), "emails": EMAILS.get(example_id, []), **used}
        print(f"{example_id}: {r['status']} in {time.time() - t:.0f}s, {len(sess.data['replies'])} replies, "
              f"{used['providers']}: {r['headline'][:110]}")
        if set(used["providers"]) != {"nightshift"}:
            print(f"   WARNING: not every call went through Nightshift: {used['providers']}")
    for example_id, scope, label in SUMMARIES:
        if example_id not in names:
            continue
        (R.EXAMPLES / example_id / "llm_replies.json").unlink(missing_ok=True)
        out = API.priority_summary(API.SummaryRequest(scope=scope, label=label, record_as=example_id))
        index[example_id] = {"example_id": example_id, "kind": "summary", "label": f"Priority summary: {label}",
                             "scope": scope, "sections": list(out["sections"])}
        print(f"{example_id}: summary of {', '.join(f'{len(v['zones'])} {k}' for k, v in out['sections'].items())}")
    R.INDEX.write_text(json.dumps({"prompts_sha": R.prompts_sha(), "recorded": time.strftime("%Y-%m-%d %H:%M"),
                                   "examples": sorted(index.values(), key=lambda e: e["example_id"])}, indent=1))


if __name__ == "__main__":
    main()
