"""data/manifest.json: what each stored source holds and how fresh it is.

Every build script calls record() after writing its outputs. For each source the manifest
keeps the data period it covers (e.g. Ookla "2026Q3"), when its inputs were downloaded,
when it was built, the refresh cadence, and per output file the row count and SHA-256.
Evidence items take their data_period / as_of from here via evidence_meta(); live sources
use their fetch time instead (live_meta()).

The manifest is small and committed, so everyone can see which data versions are in use.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pyarrow.parquet as pq

from coverage_detective.config import PROCESSED, ROOT

MANIFEST_PATH = ROOT / "data" / "manifest.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def file_date(path: Path) -> str:
    """Download date of a raw file: its .downloaded marker if any, else its mtime."""
    marker = path.with_name(path.name + ".downloaded")
    if marker.exists():
        return marker.read_text().strip()
    return date.fromtimestamp(path.stat().st_mtime).isoformat()


def load() -> dict:
    if MANIFEST_PATH.exists():
        return json.loads(MANIFEST_PATH.read_text())
    return {"sources": {}}


def record(
    source: str,
    *,
    title: str,
    data_period: str,
    downloaded: dict[str, str],
    outputs: list[Path],
    refresh: str,
    licence: str,
    extra: dict | None = None,
) -> dict:
    """Write or replace one source's entry. outputs are files in data/processed/."""
    tables = {}
    for path in outputs:
        entry = {"sha256": sha256(path), "bytes": path.stat().st_size}
        if path.suffix == ".parquet":
            entry["rows"] = pq.ParquetFile(path).metadata.num_rows
        tables[path.name] = entry
    manifest = load()
    raw = manifest["sources"].get(source, {}).get("raw")  # kept from record_raw()
    manifest["sources"][source] = {
        "title": title,
        "data_period": data_period,
        "downloaded": downloaded,
        "built_at": _now(),
        "refresh": refresh,
        "licence": licence,
        "outputs": tables,
        **(extra or {}),
        **({"raw": raw} if raw else {}),
    }
    manifest["updated_at"] = _now()
    manifest["sources"] = dict(sorted(manifest["sources"].items()))
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest["sources"][source]


def record_raw(source: str, path: Path, url: str, **info) -> None:
    """Note a downloaded raw file (date, SHA-256, URL) under the source, before any build."""
    manifest = load()
    entry = manifest["sources"].setdefault(source, {})
    entry.setdefault("raw", {})[path.name] = {
        "downloaded": file_date(path), "sha256": sha256(path), "bytes": path.stat().st_size, "url": url, **info,
    }
    manifest["updated_at"] = _now()
    manifest["sources"] = dict(sorted(manifest["sources"].items()))
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + "\n")


def source_for(table: str) -> tuple[str, dict]:
    """The manifest source that produced a table, by table or file name
    (e.g. "CD_OOKLA_MOBILE.ookla_mobile_r7", "ookla_mobile_r7" or "ookla_mobile_r7.parquet")."""
    stem = table.split(".")[-1] if not table.endswith(".parquet") else table[: -len(".parquet")]
    for name, src in load()["sources"].items():
        if f"{stem}.parquet" in src.get("outputs", {}):
            return name, src
    raise KeyError(f"No manifest entry produces {table!r}; run its build script")


def evidence_meta(table: str) -> dict:
    """data_period / as_of fields for an evidence item based on a stored table."""
    name, src = source_for(table)
    dates = sorted(src.get("downloaded", {}).values())
    return {
        "source_key": name,
        "data_period": src["data_period"],
        "as_of": dates[-1] if dates else src["built_at"][:10],
    }


def live_meta(fetched_at: datetime | None = None) -> dict:
    """data_period / as_of fields for an evidence item from a live API call."""
    at = (fetched_at or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    return {"data_period": "live", "as_of": at}


def processed(*names: str) -> list[Path]:
    return [PROCESSED / n for n in names]
