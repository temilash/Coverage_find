"""One request through Meterless Nightshift, to see which model answered. Never prints the token.

    .venv/bin/python scripts/nightshift_probe.py               # "Reply with OK" on LLM_MODEL
    .venv/bin/python scripts/nightshift_probe.py --reasoning   # on LLM_MODEL_REASONING (the critic's model)
    .venv/bin/python scripts/nightshift_probe.py --schema      # also the two structured-output styles the agents use

Nightshift only (no NVIDIA direct fallback): it prints provider, model requested, routed_model, receipt id,
route, latency and whether the receipt signature checks out. Exit code 1 if Nightshift did not answer.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import httpx
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from coverage_detective.agents import llm as L  # noqa: E402


class Ok(BaseModel):
    answer: str


def send(client: L.LLM, model: str, body_extra: dict, label: str) -> bool:
    name, url, auth, why_not = client._providers()[0]
    if why_not:
        print(f"{label}: Nightshift not tried: {why_not}")
        return False
    headers = {**auth, "Accept": "application/json", "meterless-app": L.APP_ID, "meterless-scope": f"{L.APP_ID}-probe"}
    body = {"model": model, "messages": [{"role": "user", "content": "Reply with OK"}], "max_tokens": 200,
            "temperature": 0, **body_extra}
    t0 = time.monotonic()
    try:
        r = httpx.post(f"{url}/chat/completions", json=body, headers=headers, timeout=L.NIGHTSHIFT_TIMEOUT_S)
    except httpx.HTTPError as e:
        print(f"{label}: Nightshift unreachable at {url} ({type(e).__name__}). Is Nightshift running?")
        return False
    secs = time.monotonic() - t0
    if r.status_code == 401:
        print(f"{label}: HTTP 401 unauthorized: check METERLESS_RUNTIME_TOKEN / METERLESS_RUNTIME_TOKEN_FILE")
        return False
    if r.status_code >= 400:
        print(f"{label}: HTTP {r.status_code} {r.text[:300]} (is the meterless-app name {L.APP_ID!r} registered in Nightshift?)")
        return False
    data = r.json()
    rec = L.receipt(data)
    content = (data.get("choices") or [{}])[0].get("message", {}).get("content")
    print(f"{label}: provider=nightshift  model_requested={model}  routed_model={rec.get('model_answered')}  "
          f"receipt_id={rec.get('receipt_id')}  route={rec.get('route')}  latency_ms={rec.get('latency_ms')}  "
          f"signer={rec.get('signer')}  signature_valid={rec.get('signature_valid')}  ({secs:.1f} s)"
          + (f"  NOTE: {rec['note']}" if rec.get("note") else ""))
    print(f"   reply: {(content or '').strip()[:120]!r}")
    if "nemotron" not in str(rec.get("model_answered") or "").lower():
        print("   WARNING: the answering model is not a Nemotron model")
    return True


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--reasoning", action="store_true", help="use LLM_MODEL_REASONING")
    ap.add_argument("--schema", action="store_true", help="also try strict json_schema and a schema in the prompt")
    args = ap.parse_args()
    client = L.LLM()
    model = client.reasoning_model if args.reasoning else client.model
    if bad := L.model_problem(model):
        sys.exit(f"refused: {bad}")
    print(f"Nightshift at {client.nightshift_url} (token {'found' if client._token() else 'NOT found'})")
    ok = send(client, model, {"chat_template_kwargs": {"enable_thinking": False}}, "plain")
    if args.schema:  # the advisor / letter style (strict json_schema), then the critic style (schema in the prompt)
        ok &= send(client, model, {"chat_template_kwargs": {"enable_thinking": False}, "response_format": {
            "type": "json_schema", "json_schema": {"name": "Ok", "schema": L.strict_schema(Ok), "strict": True}}},
                   "strict json_schema")
        ok &= send(client, model, {"chat_template_kwargs": {"enable_thinking": args.reasoning}, "messages": [
            {"role": "user", "content": "Reply with OK. Reply with ONLY one JSON object matching this JSON schema: "
             + json.dumps(L.strict_schema(Ok))}]}, "schema in prompt")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
