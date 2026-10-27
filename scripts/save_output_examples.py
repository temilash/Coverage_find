"""Send every input_examples/example_N.json to a running app's POST /run and save the answers.

    .venv/bin/python run.py --sample &                     # or the full app
    .venv/bin/python scripts/save_output_examples.py [--url http://localhost:8000]

Writes output_examples/example_N.json (the /run response, as returned) and copies the run's JSONL
trace to output_examples/example_N.trace.jsonl when it is on this machine. The app never reads
these files.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", default="http://localhost:8000")
    args = ap.parse_args()
    out = ROOT / "output_examples"
    out.mkdir(exist_ok=True)
    inputs = sorted((ROOT / "input_examples").glob("example_*.json"), key=lambda p: int(p.stem.split("_")[1]))
    for path in inputs:
        body = json.loads(path.read_text())
        try:
            r = httpx.post(f"{args.url}/run", json=body, timeout=1900)
        except httpx.HTTPError as e:
            sys.exit(f"{path.name}: no answer from {args.url} ({type(e).__name__}); is the app running?")
        answer = r.json()
        (out / path.name).write_text(json.dumps({"request": body, "http_status": r.status_code, "response": answer},
                                                indent=1) + "\n")
        trace = ROOT / str(answer.get("trace") or "")
        if answer.get("trace") and trace.is_file():
            shutil.copy(trace, out / f"{path.stem}.trace.jsonl")
        print(f"{path.name}: HTTP {r.status_code} {answer.get('status')} {answer.get('mode') or ''}: "
              f"{answer.get('headline') or answer.get('error')}"[:200])


if __name__ == "__main__":
    main()
