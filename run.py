"""Start Coverage Detective: the web app on http://localhost:8000.

    python run.py [--port 8000] [--host 127.0.0.1] [--reload]
    python run.py --sample      # offline: DuckDB over the committed extracts (sample_data/db/) and saved
                                # live responses; SAMPLE_MODE=true in the environment does the same

Checks that the data is readable first (DuckDB over data/processed/, or the samples). No API keys.
"""

from __future__ import annotations

import argparse
import os
import sys


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    parser.add_argument("--sample", action="store_true", help="sample mode: offline, over the committed extracts")
    parser.add_argument("--host", default="127.0.0.1", help="0.0.0.0 to serve outside this machine (Docker)")
    args = parser.parse_args()
    if args.sample:
        os.environ["SAMPLE_MODE"] = "1"  # before anything reads the environment
    import uvicorn

    from coverage_detective.config import sample_mode
    from coverage_detective.data.db import status

    if sample_mode():
        print("SAMPLE MODE: DuckDB over sample_data/db/ and saved live responses. Points outside the sample "
              "extracts are refused.")
    if (state := status()) == "ok":
        print("Data: readable")
    else:
        print(f"Data problem: {state}", file=sys.stderr)
        print("The map still works if its layers are built; cell details and diagnoses will fail until this is fixed.",
              file=sys.stderr)
    uvicorn.run("coverage_detective.api.app:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
