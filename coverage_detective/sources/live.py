"""Shared fetching for live sources: timeout, one retry, a small disk cache and fallbacks.

Every live call goes through get(), which returns a LiveResult saying where the answer came
from:
  live      fetched just now (or from the disk cache within the source's TTL)
  cached    the call failed; an older cached response is used
  sample    SAMPLE_MODE=true, or the call failed with nothing cached: a saved response for
            the nearest sample place, from sample_data/<source>/<place>.json
  unavailable  the call failed and there is no cache or sample

The disk cache (data/cache/live/, gitignored) stores raw response bodies. Samples are real
responses saved by scripts/download/live_samples.py, committed, and used by tests.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import httpx

from coverage_detective.config import ROOT, SAMPLE_LOCATIONS, SAMPLES, sample_mode

CACHE_DIR = ROOT / "data" / "cache" / "live"
TIMEOUT_S = 5.0
RETRY_BACKOFF_S = 0.5
USER_AGENT = "CoverageDetective/0.1 (hackathon; github.com/temilash/Coverage_find)"

# Slugs for the sample places, in SAMPLE_LOCATIONS order.
SAMPLE_SLUGS = ["co9_1tp", "great_langdale", "london_wc2n_5du", "rhayader", "sutton_st_edmund", "remote_powys"]

# Set by scripts/download/live_samples.py: skip the cache and save every live response as
# the sample named by the call's `sample` slug.
RECORD_SAMPLES = False


@dataclass
class LiveResult:
    source: str
    url: str
    status: str                  # live / cached / sample / unavailable (clients add not_covered)
    fetched_at: str | None       # when the response was fetched (for samples: when it was saved)
    http_status: int | None = None
    body: object = None          # parsed JSON, or text for XML feeds
    error: str | None = None
    sample_place: str | None = None
    data: dict = field(default_factory=dict)  # the client's summary of body

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("body")
        # Freshness fields every evidence item carries (see manifest.live_meta()).
        d["data_period"] = "live" if self.status in ("live", "cached") else self.status
        d["as_of"] = self.fetched_at
        return d


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def km(a_lat: float, a_lon: float, b_lat: float, b_lon: float) -> float:
    """Distance in km (equirectangular; fine at these distances)."""
    x = math.radians(b_lon - a_lon) * math.cos(math.radians((a_lat + b_lat) / 2))
    return 6371.0 * math.hypot(x, math.radians(b_lat - a_lat))


def nearest_sample(lat: float | None, lon: float | None) -> str:
    """Slug of the sample place nearest to a point (London when no point is given)."""
    if lat is None or lon is None:
        return "london_wc2n_5du"
    dists = [math.hypot(lat - la, (lon - lo) * math.cos(math.radians(lat))) for _, la, lo in SAMPLE_LOCATIONS]
    return SAMPLE_SLUGS[dists.index(min(dists))]


def _key(url: str, params: dict | None) -> str:
    raw = url + "?" + json.dumps(params or {}, sort_keys=True)
    return hashlib.sha1(raw.encode()).hexdigest()[:20]


def _read(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _write(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=1, ensure_ascii=False))


def sample_path(source: str, slug: str) -> Path:
    return SAMPLES / source / f"{slug}.json"


def _from_record(source: str, url: str, status: str, rec: dict, **extra) -> LiveResult:
    return LiveResult(source=source, url=rec.get("url", url), status=status, fetched_at=rec.get("fetched_at"),
                      http_status=rec.get("http_status"), body=rec.get("body"), **extra)


def get(source: str, url: str, params: dict | None = None, *, ttl_s: float, sample: str,
        text: bool = False, ok_statuses: tuple[int, ...] = (200,)) -> LiveResult:
    """GET url with caching and fallbacks. `sample` is the sample file slug to fall back on.

    Responses with a status in ok_statuses (e.g. 404 = postcode not found) are answers,
    cached like any other; anything else counts as a failure.
    """
    if sample_mode():
        rec = _read(sample_path(source, sample))
        if rec:
            return _from_record(source, url, "sample", rec, sample_place=sample)
        return LiveResult(source, url, "unavailable", None, error=f"SAMPLE_MODE: no sample {source}/{sample}")

    cache_file = CACHE_DIR / source / f"{_key(url, params)}.json"
    cached = _read(cache_file)
    if cached and not RECORD_SAMPLES and time.time() - cached.get("cached_at", 0) < ttl_s:
        return _from_record(source, url, "live", cached)

    error = None
    for attempt in range(2):  # one retry
        try:
            r = httpx.get(url, params=params, timeout=TIMEOUT_S, headers={"User-Agent": USER_AGENT},
                          follow_redirects=True)
            if r.status_code in ok_statuses:
                body = r.text if text else r.json()
                rec = {"url": str(r.url), "fetched_at": now_iso(), "cached_at": time.time(),
                       "http_status": r.status_code, "body": body}
                _write(cache_file, rec)
                if RECORD_SAMPLES:
                    _write(sample_path(source, sample), {k: v for k, v in rec.items() if k != "cached_at"})
                return _from_record(source, url, "live", rec)
            error = f"HTTP {r.status_code}"
            if r.status_code < 500 and r.status_code != 429:
                break  # a client error will not fix itself on retry
        except (httpx.HTTPError, ValueError) as e:
            error = f"{type(e).__name__}: {e}"
        if attempt == 0:
            time.sleep(RETRY_BACKOFF_S)

    if cached:
        return _from_record(source, url, "cached", cached, error=error)
    rec = _read(sample_path(source, sample))
    if rec:
        return _from_record(source, url, "sample", rec, error=error, sample_place=sample)
    return LiveResult(source, url, "unavailable", None, error=error)
