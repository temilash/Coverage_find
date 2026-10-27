"""Code guard, run after every agent: claims must rest on evidence in Meterless.

1. Citations: every cited evidence id must exist in Meterless for this run. Unknown ids are
   removed (lists and [bracket] mentions); a belief left with no supporting evidence is dropped.
2. Context only: [prior_diagnoses] (earlier runs on the same data) is removed from a belief's
   evidence lists; it may be mentioned, never cited as support.
3. Weak evidence: a belief whose supporting evidence is all weak is capped at 0.5. Weak is decided
   here, not by the model: an item labelled weak, an Ookla figure from fewer than 30 tests, mast
   sites from crowd-sourced data only (no operator confirmed by Ofcom's licence register), sites
   with unknown operators, or Met Office warnings that cover the region but not this district.
4. Numbers: every number in agent text must match a value in the evidence that output cites,
   within rounding and simple unit changes (3.84 -> "3.8 km", 288.4 -> "288 m", 3838 m ->
   "3.8 km", 0.25 -> "25%"). Integers 0-4 (counts of operators, rounds, ranks) are allowed.
   Offending sentences go back to the same agent once to rewrite; if they still fail they are
   removed. Every intervention is recorded in Meterless as a "guard" note.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

CAP_WEAK_ONLY = 0.5
MIN_TESTS = 30          # Ookla figures from fewer tests are weak
CONTEXT_ONLY = {"prior_diagnoses"}
FREE_INTEGERS = {0, 1, 2, 3, 4}
_BRACKET = re.compile(r"\[([A-Za-z0-9_]+)\]")
_URL = re.compile(r"https?://\S+")
_NUMBER = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?(?![\w])")
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z\[(\"'])")


@dataclass
class Intervention:
    kind: str  # unknown_citation | context_only | dropped_belief | weak_cap | unexplained_change |
               # number_rewritten | sentence_removed | verdict_changed
    summary: str
    details: dict = field(default_factory=dict)


def cited_in_text(text: str) -> set[str]:
    return set(_BRACKET.findall(text or ""))


def numbers_in(text: str) -> list[tuple[float, int, str]]:
    """(value, decimals, as written) for each number, ignoring evidence ids and URLs."""
    text = _URL.sub(" ", _BRACKET.sub(" ", text or ""))
    out = []
    for m in _NUMBER.finditer(text):
        whole, frac = m.group(1).replace(",", ""), m.group(2)
        out.append((float(f"{whole}.{frac}" if frac else whole), len(frac or ""), m.group(0)))
    return out


def weak_reason(item: dict) -> str | None:
    """Why an evidence item counts as weak for the cap, or None."""
    size, value = item.get("sample_size") or {}, item.get("value") or {}
    if item.get("strength") == "weak":
        return "labelled weak"
    if isinstance(size.get("tests"), (int, float)) and size["tests"] < MIN_TESTS:
        return f"{size['tests']:.0f} tests"
    if isinstance(value, dict) and "Met Office" in (item.get("source") or ""):
        ws = value.get("warnings") or []
        if ws and not any(w.get("district_listed") for w in ws):
            return "region-wide warning, not this district"
    if isinstance(value, dict) and "Approximate mast sites" in (item.get("source") or ""):
        if size.get("sites_unknown_operator"):
            return "sites with unknown operators"
        if not value.get("operators_confirmed"):
            return "crowd-sourced sites only"
    return None


def evidence_numbers(items: list[dict]) -> set[float]:
    """Every number in the cited evidence: in the finding text, in its values, and in the dates it
    carries (a letter may quote "data 2021, as of 2026-10-25" without inventing anything)."""
    vals: set[float] = set()

    def walk(x):
        if isinstance(x, bool) or x is None:
            return
        if isinstance(x, (int, float)):
            vals.add(float(x))
        elif isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, (list, tuple)):
            for v in x:
                walk(v)
        elif isinstance(x, str):
            for v, _, _ in numbers_in(x):
                vals.add(v)

    for it in items:
        for v, _, _ in numbers_in(it.get("finding", "")):
            vals.add(v)
        walk(it.get("value"))
        walk(it.get("sample_size"))
        for key in ("data_period", "as_of"):
            walk(str(it.get(key) or ""))
    return vals


def number_ok(value: float, decimals: int, allowed: set[float]) -> bool:
    if decimals == 0 and value in FREE_INTEGERS:
        return True
    tol = 0.5 * 10 ** -decimals + 1e-9
    for v in allowed:
        for c in (v, v / 1000, v * 1000, v * 100, v / 100, abs(v)):
            if abs(round(c, decimals) - value) <= 1e-9 or abs(c - value) <= tol:
                return True
    return False


def bad_sentences(text: str, allowed: set[float]) -> list[str]:
    return [s for s in _SENTENCE.split(text or "") if any(not number_ok(v, d, allowed) for v, d, _ in numbers_in(s))]


def map_cause_citations(ids: list[str], text_fields: dict[str, str], beliefs: dict[str, dict]) -> tuple[list[str], dict[str, str]]:
    """Map cause names an agent cited (e.g. [Terrain_Blocking]) to the matching belief id, in lists and text."""
    by_cause = {str(b.get("cause") or bid).lower(): bid for bid, b in beliefs.items()}

    def one(i: str) -> str:
        return i if i in beliefs else by_cause.get(i.lower(), i)

    fields = {k: _BRACKET.sub(lambda m: f"[{one(m.group(1))}]", v or "") for k, v in text_fields.items()}
    return [one(i) for i in ids], fields


class Guard:
    """Applies the checks for one run; `evidence` maps id -> item (from Meterless).

    `beliefs` (id -> belief) is given only for agents that may cite the reviewed beliefs (the advisor):
    a belief id is then a valid citation, and its numbers are those of the belief's supporting evidence.
    """

    def __init__(self, evidence: dict[str, dict], record: Callable[[Intervention], None],
                 beliefs: dict[str, dict] | None = None):
        self.evidence = evidence
        self.record = record
        self.beliefs = beliefs or {}

    def citable(self, i: str) -> bool:
        return i in self.evidence or i in self.beliefs

    def expand(self, ids: set[str]) -> set[str]:
        """Evidence ids behind a set of citations (a belief stands for its supporting evidence)."""
        out = {i for i in ids if i in self.evidence}
        for i in ids:
            if i in self.beliefs:
                out |= set(self.beliefs[i].get("supporting") or [])
        return out

    # ---------------------------------------------------- citations

    def known(self, ids: list[str], where: str) -> list[str]:
        good = [i for i in dict.fromkeys(ids) if self.citable(i)]
        bad = [i for i in ids if not self.citable(i)]
        if bad:
            self.record(Intervention("unknown_citation", f"{where}: removed citations not in Meterless: {', '.join(bad)}",
                                     {"where": where, "removed": bad}))
        return good

    def strip_unknown_brackets(self, text: str, where: str) -> str:
        bad = [i for i in cited_in_text(text) if not self.citable(i)]
        for i in bad:
            text = text.replace(f"[{i}]", "")
        if bad:
            self.record(Intervention("unknown_citation", f"{where}: removed [{'], ['.join(bad)}] from the text",
                                     {"where": where, "removed": bad}))
        # Only runs of spaces and tabs: a newline is the letter's structure, not noise.
        text = re.sub(r"[ \t]{2,}", " ", text)
        text = re.sub(r" +([.,;:!?])", r"\1", text)
        return re.sub(r"\(\s*\)", "", text).strip()

    def context_only(self, ids: list[str], where: str) -> list[str]:
        """Remove context-only items (earlier diagnoses) from a belief's evidence."""
        drop = [i for i in ids if i in CONTEXT_ONLY]
        if drop:
            self.record(Intervention("context_only", f"{where}: removed {', '.join(drop)} (earlier diagnoses are "
                                     "context, not evidence)", {"where": where, "removed": drop}))
        return [i for i in ids if i not in CONTEXT_ONLY]

    def weak_only(self, ids: list[str]) -> dict[str, str] | None:
        """{id: why weak} when every item is weak, else None."""
        reasons = {i: weak_reason(self.evidence[i]) for i in ids}
        return reasons if ids and all(reasons.values()) else None

    # ---------------------------------------------------- numbers

    def check_numbers(self, fields: dict[str, str], cited: set[str], where: str,
                      rewrite: Callable[[list[str], list[str]], dict[str, str]] | None) -> dict[str, str]:
        """Return the fields with unsupported sentences rewritten once (by the agent) or removed."""
        allowed = evidence_numbers([self.evidence[i] for i in self.expand(set(cited)) if i in self.evidence])
        offenders = {k: bad_sentences(v, allowed) for k, v in fields.items()}
        flat = [s for v in offenders.values() for s in v]
        if not flat:
            return fields
        fixed: dict[str, str] = {}
        if rewrite:
            try:
                fixed = rewrite(flat, sorted(cited))
            except Exception:  # noqa: BLE001 - a failed rewrite falls through to removal
                fixed = {}
        out = dict(fields)
        for key, sentences in offenders.items():
            for s in sentences:
                new = fixed.get(s)
                if new and not bad_sentences(new, allowed):
                    out[key] = out[key].replace(s, new)
                    self.record(Intervention("number_rewritten", f"{where}: rewrote a sentence with an unsupported number",
                                             {"where": where, "field": key, "before": s, "after": new}))
                else:
                    out[key] = re.sub(r"[ \t]{2,}", " ", out[key].replace(s, "")).strip()
                    nums = [w for v, d, w in numbers_in(s) if not number_ok(v, d, allowed)]
                    self.record(Intervention("sentence_removed", f"{where}: removed a sentence with numbers not in the "
                                             f"cited evidence ({', '.join(nums)})",
                                             {"where": where, "field": key, "removed": s, "numbers": nums}))
        return out
