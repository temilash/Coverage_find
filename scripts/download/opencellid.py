"""Download the UK OpenCelliD cell-tower files (MCC 234 and 235).

    python scripts/download/opencellid.py

Optional: the only download that needs a key, a free OpenCelliD account token (OPENCELLID_TOKEN in
.env). Files land in data/raw/opencellid/<mcc>.csv.gz; existing files are skipped. Without a token
this prints instructions and exits without failing, and build_masts.py uses OpenStreetMap and the
Ofcom register only. The app itself never needs a key. Licence: CC BY-SA 4.0.
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from coverage_detective.config import RAW, env  # noqa: E402

URL = "https://opencellid.org/ocid/downloads"
OUT_DIR = RAW / "opencellid"
MCCS = (234, 235)

INSTRUCTIONS = """\
OPENCELLID_TOKEN is not set, so the OpenCelliD download is skipped.
  1. Create a free account at https://opencellid.org and copy your token from the
     "Tokens" page.
  2. Add it to .env (copy .env.example to .env if you have not):  OPENCELLID_TOKEN=pk....
  3. Re-run: python scripts/download/opencellid.py
Or download 234.csv.gz and 235.csv.gz by hand from https://opencellid.org/downloads.php
(United Kingdom) into data/raw/opencellid/."""


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    missing = [m for m in MCCS if not (OUT_DIR / f"{m}.csv.gz").exists()]
    for m in MCCS:
        if m not in missing:
            print(f"exists: {OUT_DIR / f'{m}.csv.gz'}")
    if not missing:
        return
    token = env("OPENCELLID_TOKEN")
    if not token:
        print(INSTRUCTIONS)
        return
    for mcc in missing:
        dest = OUT_DIR / f"{mcc}.csv.gz"
        print(f"downloading MCC {mcc} ...", flush=True)
        params = {"token": token, "type": "mcc", "file": f"{mcc}.csv.gz"}
        with httpx.stream("GET", URL, params=params, timeout=300, follow_redirects=True) as r:
            chunks = r.iter_bytes(1 << 20)
            first = next(chunks, b"")
            # Errors (bad token, daily limit) come back as short text, not gzip.
            if r.status_code != 200 or not first.startswith(b"\x1f\x8b"):
                body = (first + b"".join(chunks))[:300].decode("utf-8", "replace").replace(token, "<token>")
                print(f"  MCC {mcc}: not downloaded (HTTP {r.status_code}): {body.strip()}")
                continue
            tmp = dest.with_suffix(".part")
            with tmp.open("wb") as f:
                f.write(first)
                for chunk in chunks:
                    f.write(chunk)
        tmp.rename(dest)
        print(f"  wrote {dest} ({dest.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
