"""Client for the Meterless service (meterless_service/, port 8100): the agents' shared case file.

    from coverage_detective.memory.meterless import memory
    run_id = memory.create_run(scope="point", query="LA22 9JY", location={...})
    memory.add_evidence(run_id, item); memory.add_belief(run_id, ...); memory.audit(run_id)

H-MEM: runs, evidence, beliefs (with revisions: who, old -> new confidence, reason),
critiques, notes (guard interventions, tool calls), past diagnoses near a point.
World Model: entities, relationships (optionally scoped by an operator context), events.

If the service is down, the client logs loudly and switches to an in-memory store with the
same interface, so a run can finish (resilience only: nothing persists, and `mode` says
"fallback"). Real runs use Meterless.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from datetime import datetime, timezone
from itertools import count

import httpx

from coverage_detective.config import env

log = logging.getLogger("coverage_detective.memory")
TIMEOUT_S = 5.0
STRENGTH_CONFIDENCE = {"strong": 0.9, "moderate": 0.7, "weak": 0.4}
_reader = threading.local()  # which agent is reading the case file (set by the coordinator), for the trace


def reading_as(agent: str | None) -> None:
    """Credit this thread's next case-file reads to `agent` (None: the coordinator)."""
    _reader.agent = agent


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class MeterlessError(RuntimeError):
    pass


class Meterless:
    def __init__(self, base_url: str | None = None):
        self.base_url = (base_url or env("MEMORY_URL", "http://127.0.0.1:8100")).rstrip("/")
        self.mode = "unknown"  # service | fallback
        self._fallback: InMemoryStore | None = None
        self.reads: dict[str, list[dict]] = {}  # run_id -> case-file reads (who, what, how many), for the trace

    def _read(self, run_id: str, what: str, rows: list) -> list:
        """Record a read; repeated reads of the same thing by the same agent are counted, not repeated."""
        log_ = self.reads.setdefault(run_id, [])
        who = getattr(_reader, "agent", None) or "coordinator"
        if log_ and log_[-1]["agent"] == who and log_[-1]["read"] == what:
            log_[-1].update(times=log_[-1]["times"] + 1, count=len(rows))
        elif len(log_) < 2000:
            log_.append({"at": _now(), "agent": who, "read": what, "count": len(rows), "times": 1})
        return rows

    # ------------------------------------------------------------ transport

    def _use_fallback(self, why: str) -> InMemoryStore:
        if self._fallback is None:
            log.error("MEMORY: Meterless service unavailable at %s (%s). Using the IN-MEMORY FALLBACK: nothing "
                      "will persist and prior diagnoses are not shared. Start it with: npx tsx meterless_service/server.ts",
                      self.base_url, why)
            print(f"!! Meterless service unavailable ({why}); using the in-memory fallback")
            self._fallback = InMemoryStore()
        self.mode = "fallback"
        return self._fallback

    def _call(self, method: str, path: str, body: dict | None = None, params: dict | None = None):
        if self.mode == "fallback":
            return self._fallback.handle(method, path, body or {}, params or {})
        for attempt in range(2):
            try:
                r = httpx.request(method, self.base_url + path, json=body, params=params, timeout=TIMEOUT_S)
                self.mode = "service"
                if r.status_code == 404:
                    return None
                if r.status_code >= 400:
                    raise MeterlessError(f"{method} {path}: {r.status_code} {r.text[:200]}")
                return r.json()
            except httpx.HTTPError as e:
                if attempt == 0:
                    time.sleep(0.3)
                    continue
                return self._use_fallback(f"{type(e).__name__}").handle(method, path, body or {}, params or {})

    def health(self) -> dict:
        try:
            r = httpx.get(self.base_url + "/health", timeout=TIMEOUT_S)
            r.raise_for_status()
            self.mode = "service"
            return r.json()
        except httpx.HTTPError as e:
            return {"status": "down", "error": f"{type(e).__name__}", "mode": self.mode}

    # ------------------------------------------------------------ runs

    def create_run(self, scope: str, query: str | None = None, label: str | None = None,
                   location: dict | None = None, by: str = "coordinator") -> str:
        return self._call("POST", "/runs", {"scope": scope, "query": query, "label": label, "location": location, "by": by})["run_id"]

    def get_run(self, run_id: str) -> dict | None:
        return self._call("GET", f"/runs/{run_id}")

    def update_run(self, run_id: str, status: str | None = None, progress: dict | None = None,
                   result: dict | None = None, by: str = "coordinator") -> dict | None:
        body = {"by": by, **({"status": status} if status else {}), **({"progress": progress} if progress else {}),
                **({"result": result} if result is not None else {})}
        return self._call("PATCH", f"/runs/{run_id}", body)

    # ------------------------------------------------------------ H-MEM

    def add_evidence(self, run_id: str, item: dict, by: str = "investigator") -> dict:
        return self._call("POST", f"/runs/{run_id}/evidence", {**item, "evidence_id": item.get("evidence_id") or item["id"], "by": by})

    def evidence(self, run_id: str) -> list[dict]:
        return self._read(run_id, "hmem:evidence", self._call("GET", f"/runs/{run_id}/evidence") or [])

    def add_belief(self, run_id: str, cause: str, confidence: float, explanation: str, supporting: list[str],
                   contradicting: list[str], round: int, by: str = "diagnostician", belief_id: str | None = None) -> dict:
        return self._call("POST", f"/runs/{run_id}/beliefs", {
            "belief_id": belief_id or cause, "cause": cause, "confidence": confidence, "explanation": explanation,
            "supporting": supporting, "contradicting": contradicting, "round": round, "by": by})

    def update_belief(self, run_id: str, belief_id: str, confidence: float | None, reason: str, by: str, round: int,
                      **fields) -> dict | None:
        return self._call("PATCH", f"/runs/{run_id}/beliefs/{belief_id}",
                          {"confidence": confidence, "reason": reason, "by": by, "round": round, **fields})

    def beliefs(self, run_id: str) -> list[dict]:
        return self._read(run_id, "hmem:beliefs", self._call("GET", f"/runs/{run_id}/beliefs") or [])

    def add_critique(self, run_id: str, critique: dict, round: int, by: str = "critic") -> dict:
        return self._call("POST", f"/runs/{run_id}/critiques", {**critique, "round": round, "by": by})

    def critiques(self, run_id: str) -> list[dict]:
        return self._read(run_id, "hmem:critiques", self._call("GET", f"/runs/{run_id}/critiques") or [])

    def add_note(self, run_id: str, type: str, summary: str, by: str, **payload) -> dict:
        return self._call("POST", f"/runs/{run_id}/notes", {"type": type, "summary": summary, "by": by, **payload})

    def notes(self, run_id: str) -> list[dict]:
        return self._call("GET", f"/runs/{run_id}/notes") or []

    def audit(self, run_id: str) -> list[dict]:
        return self._call("GET", f"/runs/{run_id}/audit") or []

    def record_diagnosis(self, run_id: str, lat: float, lon: float, **fields) -> dict:
        return self._call("POST", "/diagnoses", {"run_id": run_id, "lat": lat, "lon": lon, **fields})

    def diagnoses_near(self, lat: float, lon: float, km: float = 10.0, exclude_run: str | None = None) -> list[dict]:
        params = {"lat": lat, "lon": lon, "km": km, **({"exclude_run": exclude_run} if exclude_run else {})}
        found = self._call("GET", "/diagnoses/near", params=params) or []
        return self._read(exclude_run, "hmem:diagnoses_near", found) if exclude_run else found

    # ------------------------------------------------------------ World Model

    def upsert_entity(self, type: str, key: str, name: str | None = None, attrs: dict | None = None,
                      run_id: str | None = None, by: str = "coordinator") -> dict:
        return self._call("POST", "/world/entities", {"type": type, "key": key, "name": name, "attrs": attrs or {},
                                                       "run_id": run_id, "by": by})

    def relate(self, from_id: str, type: str, to_id: str, context: dict | None = None, attrs: dict | None = None,
               run_id: str | None = None, by: str = "coordinator") -> dict:
        return self._call("POST", "/world/relationships", {"from": from_id, "type": type, "to": to_id, "context": context,
                                                            "attrs": attrs, "run_id": run_id, "by": by})

    def add_event(self, kind: str, subject: str | None, attrs: dict | None = None, run_id: str | None = None,
                  by: str = "coordinator") -> dict:
        return self._call("POST", "/world/events", {"kind": kind, "subject": subject, "attrs": attrs or {},
                                                     "run_id": run_id, "by": by})

    def world_entity(self, entity_id: str) -> dict | None:
        from urllib.parse import quote
        return self._call("GET", f"/world/entities/{quote(entity_id, safe='')}")

    def world_for_run(self, run_id: str) -> dict:
        world = self._call("GET", f"/runs/{run_id}/world") or {"entities": [], "relationships": []}
        self._read(run_id, "world:run", world.get("entities") or [])
        return world


# ---------------------------------------------------------------------------- fallback


class InMemoryStore:
    """Same routes and shapes as meterless_service/server.ts, in process. Resilience only."""

    def __init__(self):
        self.records: dict[str, dict] = {}      # memory id -> record
        self.ledger: list[dict] = []
        self.by_ref: dict[tuple, str] = {}
        self.entities: dict[str, dict] = {}
        self.relationships: list[dict] = []
        self._ids = count(1)

    def _add(self, kind, run_id, content, ref, confidence, by, payload) -> tuple[dict, bool]:
        key = (run_id, kind, ref)
        if ref and key in self.by_ref:
            return self.records[self.by_ref[key]], False
        mid = f"mem-{next(self._ids)}"
        rec = {"memory_id": mid, "kind": kind, "run_id": run_id, "ref": ref, "content": content, "confidence": confidence,
               "source": by, "created_at": _now(), **payload}
        self.records[mid] = rec
        if ref:
            self.by_ref[key] = mid
        self.ledger.append({"memory_id": mid, "action": "create", "actor": payload.get("by", by), "at": _now(),
                            "new_confidence": confidence, "old_confidence": None, "reason": None})
        return rec, True

    def _run_recs(self, run_id, kind=None):
        return [r for r in self.records.values() if r["run_id"] == run_id and (kind is None or r["kind"] == kind)]

    def _belief_view(self, mid):
        rec = self.records[mid]
        hist = [{"at": e["at"], "by": e["actor"], "confidence": e["new_confidence"], "previous": e["old_confidence"],
                 "reason": e["reason"] or "proposed", "round": e.get("round", rec.get("round"))}
                for e in self.ledger if e["memory_id"] == mid and e["new_confidence"] is not None]
        return {**rec, "history": hist}

    def handle(self, method, path, body, params):  # noqa: C901 - a small router
        parts = path.strip("/").split("/")
        if path == "/health":
            return {"status": "ok", "mode": "fallback"}
        if method == "POST" and path == "/runs":
            run_id = f"run_mem_{int(time.time() * 1000)}"
            self._add("run", run_id, f"Diagnosis run for {body.get('label') or body.get('query')}", run_id, 1.0,
                      body.get("by", "coordinator"), {**body, "status": "running", "progress": [], "created": _now()})
            return {"run_id": run_id}
        if parts[0] == "runs":
            run_id = parts[1]
            if not self._run_recs(run_id, "run"):  # a run started before the service went down
                self._add("run", run_id, "Run (recovered in fallback)", run_id, 1.0, "coordinator",
                          {"status": "running", "progress": [], "created": _now()})
            run = self._run_recs(run_id, "run")[0]
            sub = parts[2] if len(parts) > 2 else None
            if sub is None and method == "GET":
                counts = {}
                for r in self._run_recs(run_id):
                    counts[r["kind"]] = counts.get(r["kind"], 0) + 1
                return {**run, "counts": counts}
            if sub is None and method == "PATCH":
                if body.get("status"):
                    run["status"] = body["status"]
                if body.get("progress"):
                    run["progress"].append({"at": _now(), **body["progress"]})
                if "result" in body:
                    run["result"] = body["result"]
                return run
            if sub == "evidence" and method == "POST":
                rec, created = self._add("evidence", run_id, body.get("finding", ""), body["evidence_id"],
                                         STRENGTH_CONFIDENCE.get(body.get("strength"), 0.6), body.get("by", "investigator"), body)
                return {"evidence_id": body["evidence_id"], "memory_id": rec["memory_id"], "created": created}
            if sub in ("evidence", "critiques", "notes") and method == "GET":
                return self._run_recs(run_id, {"evidence": "evidence", "critiques": "critique", "notes": "note"}[sub])
            if sub == "beliefs" and method == "POST":
                bid = body.get("belief_id") or body["cause"]
                rec, created = self._add("belief", run_id, f"{body['cause']}: {body.get('explanation', '')}", bid,
                                         body.get("confidence", 0.5), body.get("by", "diagnostician"),
                                         {**body, "belief_id": bid, "status": "active"})
                return {"belief_id": bid, "memory_id": rec["memory_id"], "created": created}
            if sub == "beliefs" and method == "GET":
                return [self._belief_view(r["memory_id"]) for r in self._run_recs(run_id, "belief")]
            if sub == "beliefs" and method == "PATCH":
                mid = self.by_ref.get((run_id, "belief", parts[3]))
                if not mid:
                    return None
                rec = self.records[mid]
                for k in ("explanation", "supporting", "contradicting", "status"):
                    if body.get(k) is not None:
                        rec[k] = body[k]
                if body.get("confidence") is not None or body.get("reason"):
                    old = rec["confidence"]
                    rec["confidence"] = body["confidence"] if body.get("confidence") is not None else old
                    self.ledger.append({"memory_id": mid, "action": "update", "actor": body.get("by"), "at": _now(),
                                        "old_confidence": old, "new_confidence": rec["confidence"],
                                        "reason": body.get("reason"), "round": body.get("round")})
                return self._belief_view(mid)
            if sub in ("critiques", "notes") and method == "POST":
                kind = "critique" if sub == "critiques" else "note"
                n = len(self._run_recs(run_id, kind)) + 1
                ref = f"{kind[0]}{n}"
                rec, _ = self._add(kind, run_id, body.get("reason") or body.get("summary", ""), ref, 1.0,
                                   body.get("by", kind), {**body, f"{kind}_id": ref})
                return {f"{kind}_id": ref, "memory_id": rec["memory_id"]}
            if sub == "audit":
                ids = {r["memory_id"] for r in self._run_recs(run_id)}
                out = []
                for seq, e in enumerate(self.ledger):
                    if e["memory_id"] in ids:
                        r = self.records[e["memory_id"]]
                        out.append({"seq": seq, "at": e["at"], "action": e["action"], "actor": e["actor"], "kind": r["kind"],
                                    "ref": r["ref"], "memory_id": e["memory_id"], "round": e.get("round", r.get("round")),
                                    "summary": r["content"] if e["action"] == "create" else e["reason"],
                                    "old_confidence": e["old_confidence"], "new_confidence": e["new_confidence"],
                                    "reason": e["reason"], "payload": r if e["action"] == "create" else None})
                return out
            if sub == "world":
                rels = [r for r in self.relationships if r.get("run_id") == run_id]
                ids = {i for r in rels for i in (r["from"], r["to"])} | {e["id"] for e in self.entities.values() if e.get("run_id") == run_id}
                return {"entities": [self.entities[i] for i in ids if i in self.entities], "relationships": rels}
        if method == "POST" and path == "/diagnoses":
            rec, _ = self._add("diagnosis", body["run_id"], body.get("headline", ""), "diagnosis", body.get("confidence", 0.7),
                               body.get("by", "coordinator"), body)
            return {"memory_id": rec["memory_id"]}
        if path == "/diagnoses/near":
            lat, lon, km = float(params["lat"]), float(params["lon"]), float(params.get("km", 10))
            out = []
            for r in self.records.values():
                if r["kind"] == "diagnosis" and r["run_id"] != params.get("exclude_run"):
                    d = _haversine_km(lat, lon, r["lat"], r["lon"])
                    if d <= km:
                        out.append({**r, "distance_km": round(d, 2)})
            return sorted(out, key=lambda x: x["distance_km"])
        if path == "/world/entities" and method == "POST":
            eid = f"{body['type']}:cd:{body['key']}"
            e = self.entities.setdefault(eid, {"id": eid, "type": body["type"], "name": body.get("name"), "attrs": {}})
            e["attrs"].update(body.get("attrs") or {})
            e["run_id"] = body.get("run_id")
            e["source"] = {"kind": "agent", "by": body.get("by"), "at": _now(), "runId": body.get("run_id")}
            return e
        if path == "/world/relationships" and method == "POST":
            rel = {"id": f"rel_{len(self.relationships) + 1}", **body,
                   "source": {"kind": "agent", "by": body.get("by"), "at": _now(), "runId": body.get("run_id")}}
            self.relationships.append(rel)
            return rel
        if path == "/world/events" and method == "POST":
            eid = f"event:cd:{body['kind']}:{body.get('run_id')}:{len(self.entities)}"
            self.entities[eid] = {"id": eid, "type": "event", "name": body["kind"], "attrs": {"kind": body["kind"], **body.get("attrs", {})},
                                  "run_id": body.get("run_id"),
                                  "source": {"kind": "agent", "by": body.get("by"), "at": _now(), "runId": body.get("run_id")}}
            if body.get("subject"):
                self.relationships.append({"id": f"rel_{len(self.relationships) + 1}", "from": eid, "type": "concerns",
                                           "to": body["subject"], "run_id": body.get("run_id"),
                                           "source": self.entities[eid]["source"]})
            return self.entities[eid]
        if parts[0] == "world" and parts[1] == "entities" and method == "GET":
            from urllib.parse import unquote
            eid = unquote("/".join(parts[2:]))
            if eid not in self.entities:
                return None
            return {"entity": self.entities[eid], "relationships": [r for r in self.relationships if eid in (r["from"], r["to"])], "facts": []}
        raise MeterlessError(f"fallback: no route {method} {path}")


def _haversine_km(a, b, c, d):
    r = math.pi / 180
    x = math.sin((c - a) * r / 2) ** 2 + math.cos(a * r) * math.cos(c * r) * math.sin((d - b) * r / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(x))


memory = Meterless()
