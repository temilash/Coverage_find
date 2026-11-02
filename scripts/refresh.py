"""Refresh stored sources on their natural cadence.

    python scripts/refresh.py --check-only            # what is due, change nothing
    python scripts/refresh.py                         # refresh everything that is due
    python scripts/refresh.py --source ookla          # only this source (repeatable)
    python scripts/refresh.py --source osm --force    # refresh even if not due

Cadence: ookla when a newer quarter is published; opencellid weekly; osm and wtr (Ofcom's
Wireless Telegraphy Register) monthly; ofcom
when a newer release zip is in data/raw/ofcom/ (Ofcom has no API: download it by hand
from the Connected Nations pages); cells (WorldPop, ONS) and dem are manual.

A refresh downloads the new raw data (the previous file is kept as *.prev until the new
one is in, then removed), rebuilds the affected tables (DuckDB reads the new Parquet files
directly; restart the app to pick them up) and updates data/manifest.json (the build scripts write it). Live sources (postcodes,
weather, warnings, floods) are fetched at question time and are not handled here.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from coverage_detective.config import PROCESSED, RAW  # noqa: E402
from coverage_detective.data import manifest  # noqa: E402

PY = sys.executable
OFCOM_PAGE = "https://www.ofcom.org.uk/phones-and-broadband/coverage-and-speeds"


@dataclass
class Status:
    due: bool
    current: str
    note: str


def run(*args: str) -> None:
    print(f"  $ {' '.join(args)}", flush=True)
    subprocess.run([PY, *args], cwd=ROOT, check=True)


def age_days(path: Path) -> int | None:
    return (date.today() - date.fromisoformat(manifest.file_date(path))).days if path.exists() else None


def swap_and_download(files: list[Path], download_script: str) -> None:
    """Move the current files aside, download fresh ones, restore the old ones on failure."""
    for f in files:
        if f.exists():
            f.rename(f.with_name(f.name + ".prev"))
    try:
        run(download_script)
        missing = [f.name for f in files if not f.exists()]
        if missing:
            raise RuntimeError(f"download did not produce {missing}")
    except Exception:
        for f in files:
            prev = f.with_name(f.name + ".prev")
            if prev.exists() and not f.exists():
                prev.rename(f)
        raise
    for f in files:
        f.with_name(f.name + ".prev").unlink(missing_ok=True)
        f.with_name(f.name + ".downloaded").write_text(date.today().isoformat() + "\n")


# --- ookla --------------------------------------------------------------------------------


def check_ookla() -> Status:
    sys.path.insert(0, str(ROOT / "scripts" / "download"))
    import httpx
    from ookla import list_quarters, quarter_label  # scripts/download/ookla.py

    current = manifest.load()["sources"].get("ookla", {}).get("data_period", "none")
    with httpx.Client(timeout=60) as client:
        latest = quarter_label(list_quarters(client)[-1])
    return Status(current == "none" or latest > current, current, f"latest published: {latest}")


def refresh_ookla() -> None:
    run("scripts/download/ookla.py", "--quarters", "4")
    run("scripts/process/build_ookla.py", "--force")


# --- masts: opencellid (weekly) and osm (monthly) ---------------------------------------

OCID_FILES = [RAW / "opencellid" / "234.csv.gz"]
OSM_FILES = [RAW / "osm" / "great-britain-latest.osm.pbf", RAW / "osm" / "ireland-and-northern-ireland-latest.osm.pbf"]


def check_age(files: list[Path], max_days: int) -> Status:
    ages = [age_days(f) for f in files]
    if None in ages:
        return Status(True, "missing", "not downloaded yet")
    oldest = max(ages)
    return Status(oldest >= max_days, f"downloaded {oldest} days ago", f"refresh every {max_days} days")


def refresh_masts(files: list[Path], download_script: str) -> None:
    swap_and_download(files, download_script)
    run("scripts/process/build_masts.py", "--force")  # merges the register too, if built
    rebuild_los()


def rebuild_los() -> None:
    """Line of sight runs to each cell's nearest site, so it follows the mast tables."""
    if (PROCESSED / "los_r7.parquet").exists():
        run("scripts/process/build_terrain.py", "--los")


# --- Ofcom Wireless Telegraphy Register (monthly) -------------------------------------------

WTR_FILES = [RAW / "ofcom_wtr" / "WTR.csv"]


def refresh_wtr() -> None:
    for f in WTR_FILES:  # keep the old file until the new one is in
        if f.exists():
            f.rename(f.with_name(f.name + ".prev"))
    try:
        run("scripts/download/ofcom_wtr.py")
    except Exception:
        for f in WTR_FILES:
            prev = f.with_name(f.name + ".prev")
            if prev.exists() and not f.exists():
                prev.rename(f)
        raise
    for f in WTR_FILES:
        f.with_name(f.name + ".prev").unlink(missing_ok=True)
    run("scripts/process/build_wtr.py")  # also rebuilds the mast tables
    rebuild_los()


# --- ofcom --------------------------------------------------------------------------------


def check_ofcom() -> Status:
    zips = sorted((RAW / "ofcom").glob("*_mobile_coverage_r*.zip"))
    current = manifest.load()["sources"].get("ofcom", {}).get("data_period", "none")
    if not zips:
        return Status(False, current, f"no release zip; download from {OFCOM_PAGE}")
    newest = zips[-1].stem
    if newest not in current:
        return Status(True, current, f"newer release in data/raw/ofcom: {newest}")
    return Status(False, current, f"up to date with local files; check {OFCOM_PAGE} for a newer release")


def refresh_ofcom() -> None:
    run("scripts/process/build_ofcom.py", "--force")


# --- manual -------------------------------------------------------------------------------


def manual(source: str, note: str) -> Callable[[], Status]:
    def check() -> Status:
        current = manifest.load()["sources"].get(source, {}).get("data_period", "none")
        return Status(False, current, note)
    return check


def not_automatic() -> None:
    raise RuntimeError("manual source: replace the raw files, then run its build script")


SOURCES: dict[str, tuple[str, Callable[[], Status], Callable[[], None]]] = {
    "ookla": ("quarterly", check_ookla, refresh_ookla),
    "opencellid": ("weekly", lambda: check_age(OCID_FILES, 7),
                   lambda: refresh_masts(OCID_FILES, "scripts/download/opencellid.py")),
    "osm": ("monthly", lambda: check_age(OSM_FILES, 30),
            lambda: refresh_masts(OSM_FILES, "scripts/download/osm.py")),
    "wtr": ("monthly", lambda: check_age(WTR_FILES, 30), refresh_wtr),
    "ofcom": ("new release", check_ofcom, refresh_ofcom),
    "cells": ("manual", manual("cells", "WorldPop / ONS boundaries: replace files in data/raw/, run build_cells.py --force"),
              not_automatic),
    "dem": ("manual", manual("terrain", "Copernicus GLO-30 is static; los_r7 is rebuilt after mast refreshes"),
            not_automatic),
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", action="append", choices=list(SOURCES), help="limit to this source (repeatable)")
    parser.add_argument("--check-only", action="store_true", help="report what is due; change nothing")
    parser.add_argument("--force", action="store_true", help="refresh the chosen sources even if not due")
    args = parser.parse_args()

    names = args.source or list(SOURCES)
    statuses = {}
    print(f"{'source':<11} {'cadence':<12} {'due':<4} {'current':<44} note")
    for name in names:
        cadence, check, _ = SOURCES[name]
        try:
            st = check()
        except Exception as e:  # e.g. offline
            st = Status(False, "?", f"check failed: {e}")
        statuses[name] = st
        print(f"{name:<11} {cadence:<12} {'yes' if st.due else 'no':<4} {st.current[:44]:<44} {st.note}")
    if args.check_only:
        return

    todo = [n for n in names if statuses[n].due or args.force]
    for name in todo:
        print(f"\nrefreshing {name} ...")
        try:
            SOURCES[name][2]()
        except Exception as e:
            print(f"  {name}: refresh failed: {e}")
    if not todo:
        print("\nnothing due")


if __name__ == "__main__":
    main()
