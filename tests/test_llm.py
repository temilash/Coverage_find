"""Nemotron client with a mocked endpoint: Nightshift first, NVIDIA direct fallback, schemas, corrective retry,
model fallback, receipts, no secrets or reasoning kept."""

import base64
import json
import logging

import httpx
import pytest

from coverage_detective.agents import llm as L
from coverage_detective.agents import schemas as S

GOOD = {"hypotheses": [{"id": "terrain_blocking", "cause": "terrain_blocking", "confidence": 0.8,
                        "supporting_evidence": ["los_ee"], "contradicting_evidence": [], "explanation": "x [los_ee]"}],
        "revision_reasons": []}


def reply(content=None, tool_calls=None, status=200, reasoning="secret chain of thought", meterless=None):
    msg = {"content": content, "reasoning_content": reasoning, **({"tool_calls": tool_calls} if tool_calls else {})}
    body = {"choices": [{"message": msg}], "usage": {"total_tokens": 10}, **({"meterless": meterless} if meterless else {})}
    return httpx.Response(status, json=body if status == 200 else {"message": "Failed"},
                          request=httpx.Request("POST", "https://x/chat/completions"))


@pytest.fixture
def client(monkeypatch):
    """NVIDIA direct only (no Nightshift token): the model logic on its own."""
    monkeypatch.setattr(L, "_throttle", L._Throttle(1000))
    monkeypatch.setattr(L.time, "sleep", lambda s: None)
    for name in ("METERLESS_RUNTIME_TOKEN", "METERLESS_RUNTIME_TOKEN_FILE", "STRICT_MODEL", "METERLESS_FRESH_SCOPE"):
        monkeypatch.delenv(name, raising=False)
    c = L.LLM()
    c.base_url, c.model, c.reasoning_model, c._key = "https://x", "nv/fast-model", "nv/reason-model", "nvapi-SECRET"
    return c


@pytest.fixture
def nightshift(client, monkeypatch):
    monkeypatch.setenv("METERLESS_RUNTIME_TOKEN", "ns-TOKEN")
    client.nightshift_url = "http://ns/v1"
    return client


def script(monkeypatch, responses, urls=None, headers=None):
    sent = []

    def post(url, json=None, **kw):
        sent.append(json)
        if urls is not None:
            urls.append(url)
        if headers is not None:
            headers.append(kw.get("headers") or {})
        r = responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(L.httpx, "post", post)
    return sent


def test_strict_schema_is_closed_and_inlined():
    sch = L.strict_schema(S.HypothesisSet)
    assert "$defs" not in json.dumps(sch) and "$ref" not in json.dumps(sch)
    item = sch["properties"]["hypotheses"]["items"]
    assert item["additionalProperties"] is False and set(item["required"]) == set(item["properties"])
    assert "terrain_blocking" in json.dumps(item["properties"]["cause"])


def test_reasoning_uses_prompt_schema_and_drops_reasoning_text(client, monkeypatch):
    sent = script(monkeypatch, [reply(json.dumps(GOOD))])
    r = client.chat([{"role": "system", "content": "sys"}, {"role": "user", "content": "u"}], reasoning=True, schema=S.HypothesisSet)
    body = sent[0]
    assert body["model"] == "nv/reason-model" and body["chat_template_kwargs"] == {"enable_thinking": True}
    assert "response_format" not in body and "JSON schema" in body["messages"][0]["content"]
    assert r.parsed.hypotheses[0].cause == "terrain_blocking"
    assert "secret chain" not in json.dumps(r.__dict__, default=str)


def test_fast_uses_strict_json_schema(client, monkeypatch):
    sent = script(monkeypatch, [reply(json.dumps(GOOD))])
    client.chat([{"role": "system", "content": "s"}], schema=S.HypothesisSet)
    assert sent[0]["response_format"]["json_schema"]["strict"] is True
    assert sent[0]["chat_template_kwargs"] == {"enable_thinking": False}


def test_corrective_retry_on_invalid_json(client, monkeypatch):
    sent = script(monkeypatch, [reply("{not json"), reply(json.dumps(GOOD))])
    r = client.chat([{"role": "system", "content": "s"}], schema=S.HypothesisSet)
    assert r.parsed is not None and len(sent) == 2 and "did not match the schema" in sent[1]["messages"][-1]["content"]


def test_server_errors_retry_2s_then_6s_then_switch_model(client, monkeypatch):
    waits = []
    monkeypatch.setattr(L.time, "sleep", waits.append)
    sent = script(monkeypatch, [reply(status=500), reply(status=500), reply(status=500), reply(json.dumps(GOOD))])
    r = client.chat([{"role": "system", "content": "s"}], reasoning=True, schema=S.HypothesisSet)
    assert [b["model"] for b in sent] == ["nv/reason-model"] * 3 + ["nv/fast-model"]
    assert waits == [2.0, 6.0]
    assert r.model == "nv/fast-model" and "reasoning model failed" in r.fallback
    assert [x.get("wait_s") for x in r.retries[:2]] == [2.0, 6.0] and r.retries[2]["switched_to"] == "nv/fast-model"


def test_a_500_then_success_is_one_recorded_retry(client, monkeypatch):
    script(monkeypatch, [reply(status=500), reply(json.dumps(GOOD))])
    r = client.chat([{"role": "system", "content": "s"}], schema=S.HypothesisSet)
    assert r.model == "nv/fast-model" and r.fallback is None and r.retries == [
        {"provider": "nvidia_direct", "model": "nv/fast-model", "attempt": 1, "error": "HTTP 500", "wait_s": 2.0}]


def test_tool_calls_parsed(client, monkeypatch):
    calls = [{"id": "1", "function": {"name": "line_of_sight", "arguments": '{"operator": "EE"}'}}]
    script(monkeypatch, [reply(None, tool_calls=calls)])
    r = client.chat([{"role": "system", "content": "s"}], tools=[{"type": "function"}])
    assert r.tool_calls == [{"id": "1", "name": "line_of_sight", "arguments": {"operator": "EE"}}]


def test_key_never_logged(client, monkeypatch, caplog):
    script(monkeypatch, [reply(status=500)] * 6)
    with caplog.at_level(logging.DEBUG), pytest.raises(L.LLMError) as e:
        client.chat([{"role": "system", "content": "s"}], schema=S.HypothesisSet)
    assert "SECRET" not in caplog.text and "SECRET" not in str(e.value)


def test_nightshift_is_primary_with_app_scope_and_token_headers(nightshift, monkeypatch):
    urls, heads = [], []
    m = {"routed_model": "nvidia/nemotron-3.5-super-vl-120b-a12b", "receipt_id": "rcpt_1",
         "winning_route": {"provider": "nvidia", "kind": "cloud"}, "usage": {"latency_ms": 900}}
    script(monkeypatch, [reply(json.dumps(GOOD), meterless=m)], urls, heads)
    r = nightshift.chat([{"role": "system", "content": "s"}], schema=S.HypothesisSet, scope="run_1")
    assert urls == ["http://ns/v1/chat/completions"]
    assert heads[0]["Authorization"] == "Bearer ns-TOKEN" and heads[0]["meterless-app"] == "coverage-detective"
    assert heads[0]["meterless-scope"] == "coverage-detective-run_1"
    assert r.provider == "nightshift" and r.model_answered == "nvidia/nemotron-3.5-super-vl-120b-a12b"
    assert r.receipt["receipt_id"] == "rcpt_1" and r.receipt["route"] == "nvidia/cloud" and r.receipt["latency_ms"] == 900
    assert r.provider_fallback is None


def test_missing_meterless_object_is_written_down(nightshift, monkeypatch):
    script(monkeypatch, [reply(json.dumps(GOOD))])
    r = nightshift.chat([{"role": "system", "content": "s"}], schema=S.HypothesisSet)
    assert r.receipt["note"] == "no Meterless receipt in the answer"


def test_nightshift_unreachable_falls_back_to_nvidia_direct(nightshift, monkeypatch):
    urls = []
    script(monkeypatch, [httpx.ConnectError("refused"), reply(json.dumps(GOOD))], urls)
    r = nightshift.chat([{"role": "system", "content": "s"}], schema=S.HypothesisSet)
    assert urls == ["http://ns/v1/chat/completions", "https://x/chat/completions"]
    assert r.provider == "nvidia_direct" and "nightshift unreachable" in r.provider_fallback
    assert any("provider_fallback" in x for x in r.retries)


def test_nightshift_401_says_check_the_token_and_falls_back(nightshift, monkeypatch):
    script(monkeypatch, [reply(status=401), reply(json.dumps(GOOD))])
    r = nightshift.chat([{"role": "system", "content": "s"}], schema=S.HypothesisSet)
    assert r.provider == "nvidia_direct" and "unauthorized: check METERLESS_RUNTIME_TOKEN" in r.provider_fallback


def test_token_read_from_file_and_never_logged(client, monkeypatch, tmp_path, caplog):
    f = tmp_path / "proxy-token"
    f.write_text("FILE-TOKEN\n")
    monkeypatch.setenv("METERLESS_RUNTIME_TOKEN_FILE", str(f))
    client.nightshift_url, client._key = "http://ns/v1", None
    heads = []
    script(monkeypatch, [reply(status=500)] * 6, headers=heads)
    with caplog.at_level(logging.DEBUG), pytest.raises(L.LLMError) as e:
        client.chat([{"role": "system", "content": "s"}], schema=S.HypothesisSet)
    assert heads[0]["Authorization"] == "Bearer FILE-TOKEN"
    assert "FILE-TOKEN" not in caplog.text and "FILE-TOKEN" not in str(e.value) and "FILE-TOKEN" not in json.dumps(e.value.retries)


def test_auto_and_bare_model_ids_are_refused(client):
    client.model = "meterless/auto"
    assert "non-NVIDIA" in client.problem and not client.configured
    client.model = "nemotron"
    assert "full model id" in client.problem
    with pytest.raises(L.LLMError):
        client._post({"model": "nemotron"}, [])


def test_strict_model_rejects_a_non_nemotron_answer(nightshift, monkeypatch):
    monkeypatch.setenv("STRICT_MODEL", "1")
    nightshift.reasoning_model = nightshift.model  # no model switch: the rejection itself is checked
    script(monkeypatch, [reply(json.dumps(GOOD), meterless={"routed_model": "other/gpt-x", "receipt_id": "r"})])
    with pytest.raises(L.LLMError, match="not a Nemotron model"):
        nightshift.chat([{"role": "system", "content": "s"}], schema=S.HypothesisSet)


def test_receipt_signature_checked_over_sorted_compact_json():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    key = Ed25519PrivateKey.generate()
    body = {"signer": "meterlessd", "receipt_id": "r1", "model": "nvidia/nemotron-3-ultra-550b-a55b"}
    sig = key.sign(json.dumps(body, separators=(",", ":"), sort_keys=True).encode())
    pub = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    env = {"body": body, "sig_b64": base64.b64encode(sig).decode(), "pubkey_b64": base64.b64encode(pub).decode()}
    rec = L.receipt({"meterless": {"routed_model": body["model"], "receipt_id": "r1", "receipt_envelope": env}})
    assert rec["signature_valid"] is True and rec["signer"] == "meterlessd"
    env["body"] = {**body, "model": "tampered"}
    assert L.verify_envelope(env) is False
