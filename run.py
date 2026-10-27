"""Start Coverage Detective: the Meterless service (port 8100), then the web app on :8000.

    python run.py [--port 8000] [--host 127.0.0.1] [--reload] [--no-memory]
    python run.py --sample      # no Zetaris, no live calls, no LLM key: DuckDB over the committed extracts,
                                # saved live responses and the recorded agent replies (examples/);
                                # SAMPLE_MODE=true in the environment does the same

Checks Zetaris first. The Meterless service (meterless_service/, needs Node 18+ and
`npm install` there) is started as a child process and stopped with the app; if it is
already running it is reused. --no-memory skips it (agents then use the in-memory fallback).
"""

from __future__ import annotations

import argparse
import atexit
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SERVICE_DIR = ROOT / "meterless_service"
LOG = ROOT / "logs" / "meterless.log"
SAMPLE_MEMORY = {"MEMORY_PORT": "8101", "MEMORY_URL": "http://127.0.0.1:8101",
                 "MEMORY_DIR": str(ROOT / "data" / "meterless_sample")}


def sample_env() -> None:
    """Sample mode, with its own Meterless store (so it never mixes with the full app's runs)."""
    os.environ["SAMPLE_MODE"] = "1"
    os.environ.update(SAMPLE_MEMORY)


def start_memory() -> subprocess.Popen | None:
    from coverage_detective.memory.meterless import Meterless

    client = Meterless()
    if client.health().get("status") == "ok":
        print(f"Meterless: already running at {client.base_url}")
        return None
    tsx = SERVICE_DIR / "node_modules" / ".bin" / "tsx"
    if not shutil.which("node") or not tsx.exists():
        print("Meterless: NOT started (needs Node 18+ and `cd meterless_service && npm install`); "
              "agents will use the in-memory fallback", file=sys.stderr)
        return None
    LOG.parent.mkdir(exist_ok=True)
    proc = subprocess.Popen([str(tsx), "server.ts"], cwd=SERVICE_DIR, stdout=LOG.open("a"), stderr=subprocess.STDOUT)
    atexit.register(proc.terminate)
    for _ in range(100):
        if proc.poll() is not None:
            print(f"Meterless: exited early, see {LOG}", file=sys.stderr)
            return None
        h = client.health()
        if h.get("status") == "ok":
            print(f"Meterless: running at {client.base_url} ({h['records']} memory records, {h['world_events']} world events)")
            return proc
        time.sleep(0.1)
    print(f"Meterless: no health check after 10 s, see {LOG}", file=sys.stderr)
    return proc


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    parser.add_argument("--no-memory", action="store_true", help="do not start the Meterless service")
    parser.add_argument("--sample", action="store_true", help="sample mode: no Zetaris, live calls or LLM key")
    parser.add_argument("--host", default="127.0.0.1", help="0.0.0.0 to serve outside this machine (Docker)")
    args = parser.parse_args()
    from coverage_detective.config import sample_mode

    args.sample = args.sample or sample_mode()  # SAMPLE_MODE=true in the environment is the same as --sample
    if args.sample:
        sample_env()  # before anything reads the environment
    import uvicorn

    from coverage_detective.data.zetaris import status as zetaris_status

    if args.sample:
        print("SAMPLE MODE: DuckDB over sample_data/zetaris/, saved live responses, recorded agent replies "
              "(examples/). Other points inside the extracts run with fixed rules in place of the model; "
              "points outside them are refused.")
    elif (state := zetaris_status()) == "ok":  # also catches a driver restarted without our data mount
        print("Zetaris: reachable, data sources readable")
    else:
        print(f"Zetaris problem: {state}", file=sys.stderr)
        print("The map still works; cell details (which query Zetaris) will fail until this is fixed.", file=sys.stderr)
    if not args.no_memory:
        start_memory()
    uvicorn.run("coverage_detective.api.app:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
