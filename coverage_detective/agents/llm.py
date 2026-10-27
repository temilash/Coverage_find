"""NVIDIA Nemotron through Meterless Nightshift (the primary provider), NVIDIA direct as a labelled fallback.

    METERLESS_BASE_URL=http://127.0.0.1:8088/v1     Nightshift's local OpenAI-compatible endpoint (default)
    METERLESS_RUNTIME_TOKEN=                         or METERLESS_RUNTIME_TOKEN_FILE=<Nightshift state dir>/proxy-token
    LLM_MODEL=nvidia/nemotron-3.5-super-vl-120b-a12b      investigator, diagnostician, advisor, letters, fix planner
    LLM_MODEL_REASONING=nvidia/nemotron-3-ultra-550b-a55b critic (thinking on)
    LLM_BASE_URL=https://integrate.api.nvidia.com/v1      NVIDIA direct, only when Nightshift is unreachable
    LLM_API_KEY=nvapi-...                                  never logged or printed (nor is the Nightshift token)

Nightshift (details confirmed against two working Nightshift 1.2.3 integrations; re-checked in a live test):
`Authorization: Bearer <token>`, `meterless-app: coverage-detective` on every request and `meterless-scope:
coverage-detective-<run_id>` (a fresh scope per run, so Nightshift's own memory cannot bring records from
other runs into our prompts; METERLESS_FRESH_SCOPE=1 gives every call its own scope). The token is read
from the environment or Nightshift's proxy-token file at call time and never stored. Model ids are pinned:
`meterless/auto` (which can route to a non-NVIDIA model) and ids without a "/" are refused. Each answer's
top-level `meterless` object gives the answering model (`routed_model`), the receipt id, the route and the
latency; its receipt envelope is checked (Ed25519 over the body as compact sorted JSON). A missing
`meterless` object is written down as "no Meterless receipt in the answer". STRICT_MODEL=1 rejects an
answer from a non-Nemotron model. Nightshift unreachable, 401 or 5xx after the retries -> NVIDIA direct,
recorded as `provider_fallback` with the reason.

Structured output, chosen by probes on 26 Oct 2026 (repeated tries per combination):
  - tool calling (investigator): Super 3/3, ~1-2 s.
  - strict JSON schema (`response_format: json_schema`, strict) on Super, thinking off: reliable,
    4-16 s on the full evidence list. Used by the advisor, letter writer and fix planner.
  - reasoning agents (diagnostician, critic): Ultra with thinking on and the JSON schema given in
    the prompt (no constrained decoding), validated by pydantic: 4/4, 17-26 s. Ultra under strict
    json_schema degenerated on long prompts (2 of 3 ran out of tokens padding output), and Super
    with thinking was slow and flaky, so neither is used. NVIDIA's `guided_json` is rejected.
Replies are validated with pydantic; one corrective retry on invalid JSON. If a model still fails
(the endpoint has bursts of HTTP 500), the other model answers once: reasoning -> fast with strict
json_schema, fast -> reasoning model with thinking off and the schema in the prompt; the result's
`fallback` says so. Tool calling falls back to the reasoning model too (Ultra, thinking off: 3/3,
3-7 s); if both fail, the coordinator uses a fixed tool plan in code. Reasoning text
(`reasoning_content`) is never stored or returned. Timeout 45 s, 90 s with thinking on (Ultra often
needs 50-90 s, and retrying at 45 s threw that work away), 180 s through Nightshift (a model load can
take minutes); one retry on timeouts, 5xx retried after 2 s and 6 s before switching provider or model,
a shared throttle under 40 requests per minute, backoff on 429. Every retry and switch is in `retries`.
"""

from __future__ import annotations

import base64
import json
import logging
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from pydantic import BaseModel, ValidationError

from coverage_detective.config import env

log = logging.getLogger("coverage_detective.llm")
TIMEOUT_S = 45.0
THINKING_TIMEOUT_S = 90.0
NIGHTSHIFT_TIMEOUT_S = 180.0
MAX_PER_MINUTE = 38
SERVER_ERROR_WAITS_S = (2.0, 6.0)  # retry a 5xx after 2 s, then 6 s, then switch provider / model
NIGHTSHIFT_URL = "http://127.0.0.1:8088/v1"
NVIDIA_URL = "https://integrate.api.nvidia.com/v1"
APP_ID = "coverage-detective"


class LLMError(RuntimeError):
    def __init__(self, message: str, retries: list[dict] | None = None):
        super().__init__(message)
        self.retries = list(retries or [])


class _ProviderDown(LLMError):
    """This provider cannot answer (unreachable, 401, 5xx after the retries): try the next one."""


@dataclass
class LLMResult:
    model: str                    # the model requested
    content: str | None = None
    parsed: BaseModel | None = None
    tool_calls: list[dict] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    duration_ms: int = 0
    attempts: int = 1
    fallback: str | None = None  # set when one model failed and the other answered
    retries: list[dict] = field(default_factory=list)  # every retry (model, attempt, error, wait_s)
    provider: str | None = None  # nightshift / nvidia_direct (recorded / rules in sample mode)
    model_answered: str | None = None
    receipt: dict = field(default_factory=dict)  # receipt_id, route, latency_ms, signer, signature_valid, note
    provider_fallback: str | None = None  # why Nightshift did not answer, when NVIDIA direct did


class _Throttle:
    """At most MAX_PER_MINUTE request starts in any 60 s window (shared by all threads)."""

    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self.starts: deque[float] = deque()
        self.lock = threading.Lock()

    def wait(self) -> None:
        while True:
            with self.lock:
                now = time.monotonic()
                while self.starts and now - self.starts[0] > 60:
                    self.starts.popleft()
                if len(self.starts) < self.per_minute:
                    self.starts.append(now)
                    return
                sleep = 60 - (now - self.starts[0]) + 0.05
            time.sleep(sleep)


_throttle = _Throttle(MAX_PER_MINUTE)


def strict_schema(model: type[BaseModel]) -> dict:
    """A pydantic model's JSON schema, inlined and tightened for strict structured output."""
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})

    def walk(node):
        if isinstance(node, dict):
            if "$ref" in node:
                return walk(defs[node["$ref"].split("/")[-1]])
            node = {k: walk(v) for k, v in node.items() if k not in ("title", "default")}
            if node.get("type") == "object" and "properties" in node:
                node["additionalProperties"] = False
                node["required"] = list(node["properties"])
            return node
        if isinstance(node, list):
            return [walk(x) for x in node]
        return node

    return walk(schema)


def model_problem(model: str | None) -> str | None:
    """Why a configured model id is refused (None when it is fine)."""
    if not model:
        return "LLM_MODEL is not set in .env"
    if model.startswith("meterless/"):
        return f"{model} is refused: it can route to a non-NVIDIA model; pin a Nemotron id such as nvidia/nemotron-3.5-super-vl-120b-a12b"
    if "/" not in model:
        return f"{model} is refused: give the full model id (e.g. nvidia/nemotron-3-ultra-550b-a55b)"
    return None


def receipt(data: dict) -> dict:
    """What a Nightshift answer says about itself: answering model, receipt id, route, latency, signature."""
    m = data.get("meterless")
    if not isinstance(m, dict):
        return {"note": "no Meterless receipt in the answer", "model_answered": data.get("model")}
    route = m.get("winning_route") or {}
    env_ = m.get("receipt_envelope") or {}
    out = {"model_answered": m.get("routed_model") or data.get("model"), "receipt_id": m.get("receipt_id"),
           "route": "/".join(str(route[k]) for k in ("provider", "kind") if route.get(k)) or None,
           "latency_ms": (m.get("usage") or {}).get("latency_ms"),
           "signer": (env_.get("body") or {}).get("signer"), "signature_valid": verify_envelope(env_)}
    if not m.get("receipt_id"):
        out["note"] = "no receipt id in the Meterless answer"
    return out


def verify_envelope(envelope: dict) -> bool | None:
    """Ed25519 over the receipt body as compact JSON with sorted keys; None when there is nothing to check."""
    body, sig, key = envelope.get("body"), envelope.get("sig_b64"), envelope.get("pubkey_b64")
    if not (body and sig and key):
        return None
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError:
        return None
    message = json.dumps(body, separators=(",", ":"), sort_keys=True, ensure_ascii=False).encode()
    try:
        Ed25519PublicKey.from_public_bytes(base64.b64decode(key)).verify(base64.b64decode(sig), message)
        return True
    except (InvalidSignature, ValueError):
        return False


class LLM:
    def __init__(self):
        self.nightshift_url = (env("METERLESS_BASE_URL", NIGHTSHIFT_URL) or "").rstrip("/")
        self.base_url = (env("LLM_BASE_URL", NVIDIA_URL) or "").rstrip("/")  # NVIDIA direct (fallback)
        self.model = env("LLM_MODEL")
        self.reasoning_model = env("LLM_MODEL_REASONING") or self.model
        self._key = env("LLM_API_KEY")

    def _token(self) -> str | None:
        """Nightshift's runtime token: the environment, else its proxy-token file (read now, never kept)."""
        if token := env("METERLESS_RUNTIME_TOKEN"):
            return token
        path = env("METERLESS_RUNTIME_TOKEN_FILE")
        if not path:
            return None
        try:
            return Path(path).expanduser().read_text().strip() or None
        except OSError:
            return None

    @property
    def problem(self) -> str | None:
        """Why the agents cannot call a model (None when they can)."""
        bad = model_problem(self.model) or (model_problem(self.reasoning_model) if self.reasoning_model != self.model else None)
        if bad:
            return bad
        if not (self._token() or self._key):
            return ("no provider: set METERLESS_RUNTIME_TOKEN or METERLESS_RUNTIME_TOKEN_FILE for Nightshift "
                    "(or LLM_API_KEY for NVIDIA direct)")
        return None

    @property
    def configured(self) -> bool:
        return self.problem is None

    def _providers(self) -> list[tuple[str, str, dict, str | None]]:
        """(name, base url, auth headers, why it was skipped): Nightshift first, NVIDIA direct second."""
        out = []
        token = self._token()
        out.append(("nightshift", self.nightshift_url, {"Authorization": f"Bearer {token}"} if token else {},
                    None if token else "no Nightshift token (METERLESS_RUNTIME_TOKEN / METERLESS_RUNTIME_TOKEN_FILE)"))
        if self._key:
            out.append(("nvidia_direct", self.base_url, {"Authorization": f"Bearer {self._key}"}, None))
        return out

    def _post(self, body: dict, retries: list[dict], scope: str | None = None) -> tuple[dict, int, str, str | None]:
        """POST to Nightshift, else NVIDIA direct. Returns (json, attempts, provider, why the first was skipped)."""
        if self.problem:
            raise LLMError(self.problem)
        attempts, skipped = 0, None
        providers = self._providers()
        for i, (name, url, auth, why_not) in enumerate(providers):
            last = i == len(providers) - 1
            if why_not:  # not a retry: recorded once as the result's provider_fallback
                skipped = why_not
                continue
            headers = {**auth, "Accept": "application/json"}
            if name == "nightshift":
                fresh = (env("METERLESS_FRESH_SCOPE", "") or "").lower() in ("1", "true", "yes")
                headers.update({"meterless-app": APP_ID,
                                "meterless-scope": f"{APP_ID}-{scope or 'none'}" + (f"-{uuid.uuid4().hex[:8]}" if fresh else "")})
            try:
                data, n = self._post_to(name, url, headers, body, retries)
                return data, attempts + n, name, skipped
            except _ProviderDown as e:
                if last:
                    raise LLMError(str(e), retries) from None
                skipped = str(e)[:200]
                retries.append({"provider_fallback": skipped, "from": name})
                log.warning("provider %s unavailable (%s); trying the next provider", name, skipped)
        raise LLMError(f"no provider answered: {skipped}", retries)

    def _post_to(self, provider: str, url: str, headers: dict, body: dict, retries: list[dict]) -> tuple[dict, int]:
        """POST to one provider with retries; every retry is appended to `retries` (for the trace)."""
        model = body["model"]
        attempts = 0
        rate_backoff = 2.0
        server_waits = list(SERVER_ERROR_WAITS_S)
        thinking = body.get("chat_template_kwargs", {}).get("enable_thinking")
        timeout = NIGHTSHIFT_TIMEOUT_S if provider == "nightshift" else THINKING_TIMEOUT_S if thinking else TIMEOUT_S
        while True:
            attempts += 1
            _throttle.wait()
            try:
                r = httpx.post(f"{url}/chat/completions", json=body, timeout=timeout, headers=headers)
            except httpx.HTTPError as e:
                what = f"no answer within {timeout:.0f} s" if isinstance(e, httpx.TimeoutException) else type(e).__name__
                if provider == "nightshift" and isinstance(e, httpx.ConnectError):
                    raise _ProviderDown(f"nightshift unreachable at {url} ({what})", retries) from None
                if attempts >= 2:  # one retry on timeouts / connection errors
                    raise _ProviderDown(f"{provider} {model}: {what} (twice)", retries) from None
                retries.append({"provider": provider, "model": model, "attempt": attempts, "error": what, "wait_s": 0})
                continue
            if r.status_code == 429:  # rate limited: back off (not counted as the retry)
                wait = min(float(r.headers.get("retry-after") or rate_backoff), 30)
                retries.append({"provider": provider, "model": model, "attempt": attempts, "error": "HTTP 429", "wait_s": wait})
                log.warning("LLM 429 from %s %s, waiting %.0f s", provider, model, wait)
                time.sleep(wait)
                rate_backoff = min(rate_backoff * 2, 30)
                if attempts > 6:
                    raise LLMError(f"{provider} {model}: rate limited", retries)
                continue
            if r.status_code >= 500 and server_waits:  # bursts of 500s: wait 2 s, then 6 s
                wait = server_waits.pop(0)
                retries.append({"provider": provider, "model": model, "attempt": attempts, "error": f"HTTP {r.status_code}",
                                "wait_s": wait})
                time.sleep(wait)
                continue
            if r.status_code >= 500:
                raise _ProviderDown(f"{provider} {model}: HTTP {r.status_code} after retries", retries)
            if r.status_code == 401:
                hint = "check METERLESS_RUNTIME_TOKEN" if provider == "nightshift" else "check LLM_API_KEY"
                raise _ProviderDown(f"{provider}: unauthorized: {hint}", retries)
            if r.status_code >= 400:
                hint = " (is the meterless-app name registered in Nightshift?)" if provider == "nightshift" else ""
                raise LLMError(f"{provider} {model}: HTTP {r.status_code} {r.text[:200]}{hint}", retries)
            return r.json(), attempts

    def chat(self, messages: list[dict], *, reasoning: bool = False, schema: type[BaseModel] | None = None,
             tools: list[dict] | None = None, temperature: float = 0.2, max_tokens: int = 4000,
             scope: str | None = None, _corrective: bool = False) -> LLMResult:
        """One completion. reasoning=True uses the reasoning model with thinking on. scope: the run id."""
        try:
            return self._chat(messages, reasoning, schema, tools, temperature, max_tokens, _corrective, scope=scope)
        except LLMError as e:
            if self.reasoning_model == self.model:
                raise
            first_retries = e.retries + [{"model": self.reasoning_model if reasoning else self.model,
                                          "error": str(e)[:160], "switched_to": self.model if reasoning else self.reasoning_model}]
            # The other model, once: reasoning -> fast (strict json_schema); fast -> reasoning model,
            # thinking off, JSON schema in the prompt.
            other = "fast" if reasoning else "reasoning"
            log.warning("%s model failed (%s); falling back to the %s model", "reasoning" if reasoning else "fast",
                        str(e)[:120], other)
            try:
                result = self._chat(messages, not reasoning, schema, tools, temperature, max_tokens, False,
                                    thinking=False, prompt_schema=not reasoning, scope=scope)
            except LLMError as e2:
                raise LLMError(f"both models failed: {str(e)[:120]} / {str(e2)[:120]}", first_retries + e2.retries) from None
            result.retries = first_retries + result.retries
            result.fallback = f"{'reasoning' if reasoning else 'fast'} model failed ({str(e)[:140]}); {other} model answered"
            return result

    def _chat(self, messages, reasoning, schema, tools, temperature, max_tokens, _corrective,
              thinking: bool | None = None, prompt_schema: bool | None = None, scope: str | None = None) -> LLMResult:
        model = self.reasoning_model if reasoning else self.model
        thinking = reasoning if thinking is None else thinking
        prompt_schema = reasoning if prompt_schema is None else prompt_schema
        body: dict = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens,
                      "chat_template_kwargs": {"enable_thinking": thinking}}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        if schema and prompt_schema:  # the schema goes in the prompt; constrained decoding degenerates on Ultra
            instruction = ("\n\nReply with ONLY one JSON object matching this JSON schema (no prose, no code fences):\n"
                           + json.dumps(strict_schema(schema)))
            body["messages"] = [{**messages[0], "content": messages[0]["content"] + instruction}, *messages[1:]]
        elif schema:
            body["response_format"] = {"type": "json_schema",
                                       "json_schema": {"name": schema.__name__, "schema": strict_schema(schema), "strict": True}}
        t0 = time.monotonic()
        retries: list[dict] = []
        data, attempts, provider, skipped = self._post(body, retries, scope)
        rec = receipt(data) if provider == "nightshift" else {"model_answered": data.get("model") or model}
        answered = rec.pop("model_answered", None) or model
        if (env("STRICT_MODEL", "") or "").lower() in ("1", "true", "yes") and "nemotron" not in answered.lower():
            raise LLMError(f"{provider} answered with {answered}, not a Nemotron model (STRICT_MODEL=1); "
                           "is the meterless-app name registered in Nightshift?", retries)
        msg = data["choices"][0]["message"]
        result = LLMResult(model=model, content=msg.get("content"), usage=data.get("usage") or {},
                           tool_calls=[{"id": c.get("id"), "name": c["function"]["name"],
                                        "arguments": json.loads(c["function"].get("arguments") or "{}")}
                                       for c in msg.get("tool_calls") or []],
                           attempts=attempts, retries=retries, provider=provider, model_answered=answered,
                           receipt=rec, provider_fallback=skipped if provider != "nightshift" else None)
        # msg.get("reasoning_content") is deliberately dropped: raw reasoning is never stored.
        if schema:
            try:
                result.parsed = schema.model_validate_json(_strip_fences(result.content or ""))
            except ValidationError as e:
                if _corrective:
                    raise LLMError(f"{model}: invalid {schema.__name__} after a corrective retry: {str(e)[:300]}") from None
                fix = messages + [{"role": "assistant", "content": result.content or ""},
                                  {"role": "user", "content": f"That did not match the schema ({str(e)[:400]}). "
                                                              "Reply again with only the corrected JSON."}]
                retry = self._chat(fix, reasoning, schema, None, temperature, max_tokens, True, thinking, prompt_schema, scope)
                retry.attempts += attempts
                retry.usage = _add_usage(result.usage, retry.usage)
                return retry
        result.duration_ms = round((time.monotonic() - t0) * 1000)
        return result


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    return text


def _add_usage(a: dict, b: dict) -> dict:
    return {k: (a.get(k) or 0) + (b.get(k) or 0) for k in set(a) | set(b) if isinstance(a.get(k, 0), int)}


llm = LLM()
