import json
from decimal import Decimal

import anthropic
import httpx2
import pytest

import evalsuite.provider as provider_module
from evalsuite.domain import Case, Passage, Response
from evalsuite.provider import (
    INPUT_LIMIT,
    MODEL,
    OUTPUT_LIMIT,
    PREFLIGHT_INPUT_LIMIT,
    RUN_DEADLINE_SECONDS,
    TRANSIENT_FAILURE_LIMIT,
    LiveProvider,
    cost_usd,
    request_body,
)

PROMPT = "Use the policy passages."
ANSWER = '{"decision":"clarify","value":null,"evidence_ids":[],"customer_response":"Which plan?"}'


@pytest.fixture
def case() -> Case:
    return Case(
        id="private-case-id",
        category="private-category",
        cluster="private-cluster",
        question="Est-ce admissible?",
        passages=[Passage(id="policy-1", text="Eligible for active plans.")],
        expected=Response(
            decision="abstain",
            value=None,
            evidence_ids=[],
            customer_response="private-gold",
        ),
    )


@pytest.fixture
def mocked_client():
    clients = []

    def make_client(handler):
        http_client = httpx2.Client(transport=httpx2.MockTransport(handler))
        client = anthropic.Anthropic(
            api_key="not-a-real-key",
            base_url="https://api.anthropic.com",
            http_client=http_client,
        )
        clients.append(client)
        return client

    yield make_client
    for client in clients:
        client.close()


def message_response(
    *,
    stop_reason="end_turn",
    text=ANSWER,
    usage=None,
    model=MODEL,
):
    content = [{"type": "thinking", "thinking": "not answer text", "signature": "dummy"}]
    if text is not None:
        content.append({"type": "text", "text": text})
    return httpx2.Response(
        200,
        headers={"request-id": "req_inference"},
        json={
            "type": "message",
            "id": "msg_fake",
            "model": model,
            "role": "assistant",
            "content": content,
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": {"input_tokens": 120, "output_tokens": 18} if usage is None else usage,
        },
    )


def test_sdk_sends_only_public_payload_and_reads_native_output(case, mocked_client):
    calls = []

    def reply(request):
        calls.append((request.url.path, json.loads(request.content)))
        if request.url.path.endswith("/count_tokens"):
            return httpx2.Response(200, json={"input_tokens": 123})
        return message_response()

    result = LiveProvider(Decimal("0.01"), client=mocked_client(reply)).collect(case, PROMPT)
    [(preflight_path, preflight), (inference_path, inference)] = calls
    assert preflight_path == "/v1/messages/count_tokens"
    assert inference_path == "/v1/messages"
    assert inference == request_body(case, PROMPT)
    assert all(inference[key] == value for key, value in preflight.items())
    assert "max_tokens" not in preflight
    assert inference["model"] == MODEL
    assert inference["system"] == PROMPT
    assert inference["max_tokens"] == OUTPUT_LIMIT
    assert inference["output_config"]["format"]["type"] == "json_schema"
    assert set(inference["output_config"]["format"]["schema"]["required"]) == {
        "decision",
        "value",
        "evidence_ids",
        "customer_response",
    }
    assert inference["output_config"]["format"]["schema"]["additionalProperties"] is False
    assert json.loads(inference["messages"][0]["content"]) == {
        "question": case.question,
        "passages": [{"id": "policy-1", "text": "Eligible for active plans."}],
    }
    assert "private-" not in json.dumps(inference)
    assert result == {
        "text": ANSWER,
        "status": "ok",
        "error": None,
        "input_tokens": 120,
        "output_tokens": 18,
        "latency_ms": result["latency_ms"],
        "cost_usd": "0.000021",
        "model": MODEL,
        "request_id": "req_inference",
        "stop_reason": "end_turn",
    }
    assert result["latency_ms"] > 0


@pytest.mark.parametrize(
    ("stop_reason", "text"),
    [("max_tokens", '{"decision":'), ("refusal", "Refused"), ("end_turn", None)],
)
def test_completed_invalid_output_preserves_usage(case, mocked_client, stop_reason, text):
    def reply(request):
        if request.url.path.endswith("/count_tokens"):
            return httpx2.Response(200, json={"input_tokens": 123})
        return message_response(stop_reason=stop_reason, text=text)

    result = LiveProvider(Decimal("0.01"), client=mocked_client(reply)).collect(case, PROMPT)
    assert result["status"] == "invalid"
    assert result["stop_reason"] == stop_reason
    assert result["text"] == (text or "")
    assert result["cost_usd"] == "0.000021"


def test_preflight_rejects_large_count_before_inference(case, mocked_client):
    calls = []

    def reply(request):
        calls.append(request.url.path)
        return httpx2.Response(200, json={"input_tokens": PREFLIGHT_INPUT_LIMIT + 1})

    result = LiveProvider(Decimal("0.01"), client=mocked_client(reply)).collect(case, PROMPT)
    assert calls == ["/v1/messages/count_tokens"]
    assert result["status"] == "error"
    assert result["cost_usd"] == "0"


def test_transport_timeout_has_no_retries_and_holds_reservation(case, mocked_client):
    calls = []

    def reply(request):
        calls.append(request.url.path)
        if request.url.path.endswith("/count_tokens"):
            return httpx2.Response(200, json={"input_tokens": 123})
        raise httpx2.ReadTimeout("test deliberately lost the response", request=request)

    budget = cost_usd(INPUT_LIMIT, OUTPUT_LIMIT)
    provider = LiveProvider(budget, client=mocked_client(reply))
    first = provider.collect(case, PROMPT)
    second = provider.collect(case, PROMPT)
    assert calls == ["/v1/messages/count_tokens", "/v1/messages"]
    assert first["status"] == second["status"] == "error"
    assert first["cost_usd"] is None
    assert second["cost_usd"] == "0"
    assert "APITimeoutError" in first["error"]
    assert "budget" in second["error"]


def test_rate_limit_has_request_id_but_no_inferred_billing(case, mocked_client):
    calls = []

    def reply(request):
        calls.append(request.url.path)
        if request.url.path.endswith("/count_tokens"):
            return httpx2.Response(200, json={"input_tokens": 123})
        return httpx2.Response(
            429,
            headers={"request-id": "req_ratelimit"},
            json={"error": {"message": "rate limit", "type": "rate_limit_error"}},
        )

    provider = LiveProvider(Decimal("0.01"), client=mocked_client(reply))
    result = provider.collect(case, PROMPT)
    next_trial = provider.collect(case, PROMPT)
    assert calls == ["/v1/messages/count_tokens", "/v1/messages"]
    assert result["status"] == "error"
    assert result["request_id"] == "req_ratelimit"
    assert result["input_tokens"] is result["output_tokens"] is result["cost_usd"] is None
    assert next_trial["status"] == "error"
    assert next_trial["cost_usd"] == "0"


def test_actual_cost_releases_unused_reservation(case, mocked_client):
    calls = []

    def reply(request):
        calls.append(request.url.path)
        if request.url.path.endswith("/count_tokens"):
            return httpx2.Response(200, json={"input_tokens": 123})
        return message_response()

    budget = cost_usd(INPUT_LIMIT, OUTPUT_LIMIT) + cost_usd(120, 18)
    provider = LiveProvider(budget, client=mocked_client(reply))
    assert provider.collect(case, PROMPT)["status"] == "ok"
    assert provider.collect(case, PROMPT)["status"] == "ok"
    assert provider.collect(case, PROMPT)["status"] == "error"
    assert calls.count("/v1/messages") == 2


def test_unexpected_billable_usage_is_reported_and_stops_dispatch(case, mocked_client):
    calls = []

    def reply(request):
        calls.append(request.url.path)
        if request.url.path.endswith("/count_tokens"):
            return httpx2.Response(200, json={"input_tokens": 123})
        return message_response(
            usage={
                "input_tokens": 120,
                "output_tokens": 18,
                "cache_read_input_tokens": 50,
                "cache_creation_input_tokens": 30,
                "cache_creation": {
                    "ephemeral_5m_input_tokens": 10,
                    "ephemeral_1h_input_tokens": 20,
                },
            }
        )

    provider = LiveProvider(Decimal("0.01"), client=mocked_client(reply))
    actual = provider.collect(case, PROMPT)
    assert actual["status"] == "error"
    assert actual["input_tokens"] == 200
    assert actual["cost_usd"] == "0.00002675"
    assert provider.collect(case, PROMPT)["status"] == "error"
    assert calls == ["/v1/messages/count_tokens", "/v1/messages"]


@pytest.mark.parametrize("status", [400, 401, 402, 403, 404])
@pytest.mark.parametrize("fails_in_preflight", [True, False])
def test_fatal_http_error_stops_further_network_calls(
    case, mocked_client, status, fails_in_preflight
):
    calls = []

    def reply(request):
        calls.append(request.url.path)
        if request.url.path.endswith("/count_tokens") and not fails_in_preflight:
            return httpx2.Response(200, json={"input_tokens": 123})
        return httpx2.Response(
            status,
            headers={"request-id": "req_fatal"},
            json={"error": {"message": "provider diagnostic", "type": "test"}},
        )

    provider = LiveProvider(Decimal("0.01"), client=mocked_client(reply))
    first = provider.collect(case, PROMPT)
    next_trial = provider.collect(case, PROMPT)
    expected_calls = ["/v1/messages/count_tokens"]
    if not fails_in_preflight:
        expected_calls.append("/v1/messages")
    assert calls == expected_calls
    assert first["status"] == next_trial["status"] == "error"
    assert first["request_id"] == "req_fatal"
    assert first["cost_usd"] == ("0" if fails_in_preflight else None)
    assert next_trial["cost_usd"] == "0"
    assert "fatal provider error" in next_trial["error"]


def test_transient_error_limit_resets_after_a_completed_trial(case, mocked_client):
    inference_attempts = []

    def reply(request):
        if request.url.path.endswith("/count_tokens"):
            return httpx2.Response(200, json={"input_tokens": 123})
        inference_attempts.append(request.url.path)
        if len(inference_attempts) == TRANSIENT_FAILURE_LIMIT:
            return message_response()
        return httpx2.Response(503, json={"error": {"message": "temporary", "type": "test"}})

    provider = LiveProvider(Decimal("0.01"), client=mocked_client(reply))
    for _ in range(TRANSIENT_FAILURE_LIMIT - 1):
        assert provider.collect(case, PROMPT)["cost_usd"] is None
    assert provider.collect(case, PROMPT)["status"] == "ok"
    for _ in range(TRANSIENT_FAILURE_LIMIT):
        assert provider.collect(case, PROMPT)["cost_usd"] is None
    skipped = provider.collect(case, PROMPT)
    assert skipped["status"] == "error"
    assert skipped["cost_usd"] == "0"
    assert "successive transient" in skipped["error"]
    assert len(inference_attempts) == 2 * TRANSIENT_FAILURE_LIMIT


@pytest.mark.parametrize("remaining", [0, 3.0])
def test_deadline_prevents_inference_and_bounds_preflight_timeout(
    case, mocked_client, monkeypatch, remaining
):
    calls = []
    now = [100.0]
    monkeypatch.setattr(provider_module, "monotonic", lambda: now[0])

    def reply(request):
        calls.append((request.url.path, request.extensions["timeout"]["read"]))
        now[0] += remaining
        return httpx2.Response(200, json={"input_tokens": 123})

    provider = LiveProvider(Decimal("0.01"), client=mocked_client(reply))
    now[0] += RUN_DEADLINE_SECONDS - remaining
    result = provider.collect(case, PROMPT)
    assert result["status"] == "error"
    assert result["cost_usd"] == "0"
    assert "deadline" in result["error"]
    assert calls == ([("/v1/messages/count_tokens", remaining)] if remaining else [])


def test_missing_preflight_count_is_unbilled_and_stops_network(case, mocked_client):
    calls = []

    def reply(request):
        calls.append(request.url.path)
        return httpx2.Response(200, json={"input_tokens": None})

    provider = LiveProvider(Decimal("0.01"), client=mocked_client(reply))
    assert provider.collect(case, PROMPT)["cost_usd"] == "0"
    assert provider.collect(case, PROMPT)["cost_usd"] == "0"
    assert calls == ["/v1/messages/count_tokens"]


@pytest.mark.parametrize(
    "usage",
    [
        None,
        {"input_tokens": 120, "output_tokens": None},
        {"input_tokens": 120, "output_tokens": 18, "server_tool_use": {"web_fetch_requests": None}},
    ],
)
def test_nullable_usage_fields_have_known_cost_only_with_complete_required_counts(
    case, mocked_client, usage
):
    def reply(request):
        if request.url.path.endswith("/count_tokens"):
            return httpx2.Response(200, json={"input_tokens": 123})
        base = message_response()
        return httpx2.Response(200, json={**base.json(), "usage": usage})

    result = LiveProvider(Decimal("0.01"), client=mocked_client(reply)).collect(case, PROMPT)
    if usage and usage["output_tokens"] is not None:
        assert result["status"] == "ok"
        assert result["cost_usd"] == "0.000021"
    else:
        assert result["status"] == "error"
        assert result["input_tokens"] == (120 if usage else None)
        assert result["cost_usd"] is None
