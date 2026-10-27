"""Recorded agent replies for sample mode: record once with the live model, replay with no LLM key.

    record: scripts/record_examples.py runs each example with SAMPLE_MODE data and the live model, and
            saves every reply to examples/<example_id>/llm_replies.json
    replay: python run.py --sample answers each agent call from that file

Each reply is keyed by example id + agent + round + call index (the n-th call of that agent in that
round, guard rewrites included), not by call order alone. A missing key fails the run clearly with
"no recorded reply for this step"; replay never falls back to a live call. Replayed results are marked
"recorded" (model "recorded", result["replayed"]) and the pages label them.

Examples are listed in examples/index.json (place or zone, so a sample-mode request finds its example)
with the hash of the prompts they were recorded with; tests check the prompts have not changed since.
"""

from __future__ import annotations

import hashlib
import json
import math
import threading
from pathlib import Path

from pydantic import BaseModel

from coverage_detective.agents.llm import LLMResult
from coverage_detective.config import ROOT, env, sample_mode

EXAMPLES = ROOT / "examples"
INDEX = EXAMPLES / "index.json"
PROMPTS = Path(__file__).parent / "prompts"
NEAR_KM = 0.1  # a sample-mode point request within this distance replays that example, run at its recorded point
_lock = threading.Lock()


def recording() -> bool:
    """RECORD_EXAMPLES=1 (set by scripts/record_examples.py): sample data, live model, replies saved."""
    return (env("RECORD_EXAMPLES", "") or "").lower() in ("1", "true", "yes")


def replaying() -> bool:
    """Sample mode without recording: every agent reply comes from a recorded example."""
    return sample_mode() and not recording()


class ReplayMissing(RuntimeError):
    """No recorded reply for this step (never caught as an LLM failure: the run fails clearly)."""


def prompts_sha() -> str:
    h = hashlib.sha256()
    for p in sorted(PROMPTS.glob("*.md")):
        h.update(p.name.encode() + b"\0" + p.read_bytes())
    return h.hexdigest()


def key(agent: str, round: int, index: int) -> str:
    return f"{agent}|round {round}|call {index}"


def _path(example_id: str) -> Path:
    return EXAMPLES / example_id / "llm_replies.json"


def load(example_id: str) -> dict:
    p = _path(example_id)
    if not p.exists():
        raise ReplayMissing(f"no recorded replies for example {example_id!r} ({p.relative_to(ROOT)} is missing)")
    return json.loads(p.read_text())


class Session:
    """Per run: replay or record one example, counting each agent's calls per round."""

    def __init__(self, mode: str, example_id: str):
        assert mode in ("record", "replay")
        self.mode, self.example_id = mode, example_id
        self.counts: dict[tuple[str, int], int] = {}
        if mode == "replay":
            self.data = load(example_id)
        else:  # recording adds to the example's file (a letter after its diagnosis); record_examples.py empties it first
            p = _path(example_id)
            self.data = json.loads(p.read_text()) if p.exists() else {"example_id": example_id, "replies": {}}
            self.data["prompts_sha"] = prompts_sha()

    variant: str | None = None  # e.g. a letter's recipient, so each recipient has its own recorded letter

    def has(self, agent: str, variant: str) -> bool:
        """Whether replies were recorded for this agent and variant (any round)."""
        return any(k.startswith(f"{agent}:{variant}|") for k in self.data["replies"])

    def next_key(self, agent: str, round: int) -> str:
        agent = f"{agent}:{self.variant}" if self.variant else agent
        n = self.counts.get((agent, round), 0) + 1
        self.counts[(agent, round)] = n
        return key(agent, round, n)

    def reply(self, k: str, schema: type[BaseModel] | None) -> LLMResult:
        rec = self.data["replies"].get(k)
        if rec is None:
            raise ReplayMissing(f"no recorded reply for this step: example {self.example_id}, {k}")
        # provider / model_answered / receipt say who answered when the reply was recorded (absent in older recordings)
        return LLMResult(model="recorded", content=rec.get("content"), tool_calls=rec.get("tool_calls") or [],
                         parsed=schema.model_validate(rec["parsed"]) if schema and rec.get("parsed") is not None else None,
                         usage={}, duration_ms=0, provider="recorded",
                         model_answered=rec.get("model_answered") or rec.get("model"),
                         receipt={"recorded_provider": rec.get("provider"), **(rec.get("receipt") or {})})

    def save(self, k: str, r: LLMResult) -> None:
        self.data["replies"][k] = {"model": r.model, "parsed": r.parsed.model_dump() if r.parsed else None,
                                   "tool_calls": r.tool_calls, "content": None if r.parsed else r.content,
                                   "duration_ms": r.duration_ms, "provider": r.provider, "model_answered": r.model_answered,
                                   "receipt": {k: v for k, v in (r.receipt or {}).items()
                                               if k in ("receipt_id", "route", "signer", "signature_valid", "note")},
                                   "provider_fallback": r.provider_fallback}
        with _lock:
            p = _path(self.example_id)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(self.data, indent=1))


# ------------------------------------------------------------------ which example answers a request


def index() -> list[dict]:
    return json.loads(INDEX.read_text())["examples"] if INDEX.exists() else []


def _km(a_lat, a_lon, b_lat, b_lon) -> float:
    x = math.radians(b_lon - a_lon) * math.cos(math.radians((a_lat + b_lat) / 2))
    return 6371.0 * math.hypot(x, math.radians(b_lat - a_lat))


def example(example_id: str) -> dict:
    return next(e for e in index() if e["example_id"] == example_id)


def find(kind: str, lat: float | None = None, lon: float | None = None, zone_id: str | None = None,
         scope: str | None = None) -> str:
    """The example that answers a sample-mode request, or ReplayMissing with what is available."""
    ex = [e for e in index() if e["kind"] == kind]
    for e in ex:
        if kind == "zone" and e.get("zone_id") == zone_id:
            return e["example_id"]
        if kind == "summary" and e.get("scope") == scope:
            return e["example_id"]
        if kind == "point" and lat is not None and _km(lat, lon, e["lat"], e["lon"]) <= NEAR_KM:
            return e["example_id"]
    names = ", ".join(e["label"] + (f" ({e['lat']}, {e['lon']})" if kind == "point" else "") for e in ex) or "none"
    raise ReplayMissing(f"sample mode has no recorded run for this {kind}; recorded {kind}s: {names}")
