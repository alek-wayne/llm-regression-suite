"""Anthropic Messages requests and bounded live trial collection."""

from __future__ import annotations

import json
import os
from decimal import Decimal
from time import monotonic, perf_counter
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from anthropic import Anthropic
    from anthropic.types import Usage

    from .domain import Case


MODEL = "claude-haiku-5-5"
RATE_INPUT = Decimal("0.10")
RATE_OUTPUT = Decimal("0.50")
PREFLIGHT_INPUT_LIMIT = 3072
INPUT_LIMIT = 4096
OUTPUT_LIMIT = 512
TIMEOUT_SECONDS = 30.0
RUN_DEADLINE_SECONDS = 20 * 60
TRANSIENT_FAILURE_LIMIT = 3
REQUEST_BYTE_LIMIT = 32_768
UNFINISHED_STOPS = {
    "max_tokens",
    "refusal",
    "stop_sequence",
    "tool_use",
    "pause_turn",
    "model_context_window_exceeded",
}


def cost_usd(input_tokens: int, output_tokens: int) -> Decimal:
    """Public list-price cost for uncached tokens on the pinned model."""
    tier = 5 if input_tokens > 100_000 else 1
    return (RATE_INPUT * input_tokens + RATE_OUTPUT * output_tokens) * tier / 1_000_000


def request_body(case: Case, prompt: str) -> dict:
    """Build the same allowlisted request for both hashing and inference."""
    public_task = {
        "question": case.question,
        "passages": [{"id": passage.id, "text": passage.text} for passage in case.passages],
    }
    schema_properties = {
        "decision": {"type": "string", "enum": ["answer", "clarify", "abstain"]},
        "value": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "evidence_ids": {"type": "array", "items": {"type": "string"}},
        "customer_response": {"type": "string"},
    }
    return {
        "model": MODEL,
        "max_tokens": OUTPUT_LIMIT,
        "system": prompt,
        "messages": [{"role": "user", "content": json.dumps(public_task, ensure_ascii=False)}],
        "thinking": {"type": "disabled"},
        "service_tier": "standard_only",
        "inference_geo": "global",
        "output_config": {
            "format": {
                "type": "json_schema",
                "schema": {
                    "type": "object",
                    "properties": schema_properties,
                    "required": list(schema_properties),
                    "additionalProperties": False,
                },
            }
        },
    }


def _token_count(value: object) -> int | None:
    if type(value) is int and value >= 0:
        return value
    return None


def _reported_usage(usage: Usage | None) -> tuple[int | None, int | None, Decimal | None, bool]:
    """Price observable token use and identify departures from the expected setup."""
    uncached = _token_count(getattr(usage, "input_tokens", None))
    output = _token_count(getattr(usage, "output_tokens", None))
    read_count = getattr(usage, "cache_read_input_tokens", None)
    write_count = getattr(usage, "cache_creation_input_tokens", None)
    cache_read = _token_count(0 if read_count is None else read_count)
    cache_write = _token_count(0 if write_count is None else write_count)
    if uncached is None or cache_read is None or cache_write is None:
        return None, output, None, False

    input_total = uncached + cache_read + cache_write
    if output is None:
        return input_total, None, None, False

    tier = getattr(usage, "service_tier", None)
    speed = getattr(usage, "speed", None)
    tools = getattr(usage, "server_tool_use", None)
    if (
        tier not in (None, "standard")
        or speed not in (None, "standard")
        or (
            tools is not None
            and (
                getattr(tools, "web_fetch_requests", None) not in (None, 0)
                or getattr(tools, "web_search_requests", None) not in (None, 0)
            )
        )
    ):
        return input_total, output, None, False

    # The global routing requested in request_body uses standard geography pricing.
    cost = cost_usd(input_total, output)
    if cache_read or cache_write:
        breakdown = getattr(usage, "cache_creation", None)
        write_5m = _token_count(getattr(breakdown, "ephemeral_5m_input_tokens", None))
        write_1h = _token_count(getattr(breakdown, "ephemeral_1h_input_tokens", None))
        if (cache_write or breakdown is not None) and (
            write_5m is None or write_1h is None or write_5m + write_1h != cache_write
        ):
            return input_total, output, None, False
        extra_cost_tokens = Decimal("0.25") * (write_5m or 0) + (write_1h or 0)
        extra_cost_tokens -= Decimal("0.90") * cache_read
        long_context_multiplier = 5 if input_total > 100_000 else 1
        cost += RATE_INPUT * long_context_multiplier * extra_cost_tokens / 1_000_000

    standard_usage = (
        input_total <= INPUT_LIMIT and output <= OUTPUT_LIMIT and not cache_read and not cache_write
    )
    return input_total, output, cost, standard_usage


class LiveProvider:
    """Make synchronous, no-retry requests under a per-run spend estimate."""

    def __init__(self, budget_usd: Decimal, *, client: Anthropic | None = None) -> None:
        import anthropic

        if not budget_usd.is_finite() or budget_usd <= 0:
            raise ValueError("Live budget must be a finite, positive USD amount.")
        self._remaining = budget_usd
        self._halt_reason = None
        self._consecutive_transient_errors = 0
        self._errors = (anthropic.APIError, ValueError)
        self._connection_error = anthropic.APIConnectionError
        self._own_client = client is None
        if client is None:
            api_key = os.environ.get("ANTHROPIC_API_KEY")
            if not api_key:
                raise ValueError("Set ANTHROPIC_API_KEY to use live mode.")
            client = anthropic.Anthropic(
                api_key=api_key,
                base_url="https://api.anthropic.com",
                max_retries=0,
                timeout=TIMEOUT_SECONDS,
            )
        else:
            client = client.with_options(max_retries=0, timeout=TIMEOUT_SECONDS)
        self._client = client
        self._deadline = monotonic() + RUN_DEADLINE_SECONDS

    def close(self) -> None:
        if self._own_client:
            self._client.close()

    def collect(self, case: Case, prompt: str) -> dict:
        start = perf_counter()
        result = {
            "text": "",
            "status": "error",
            "error": None,
            "input_tokens": None,
            "output_tokens": None,
            "latency_ms": None,
            "cost_usd": "0",
            "model": MODEL,
            "request_id": None,
            "stop_reason": None,
        }
        self._collect(case, prompt, result)
        result["latency_ms"] = (perf_counter() - start) * 1_000
        return result

    def _collect(self, case: Case, prompt: str, result: dict) -> None:
        reservation = cost_usd(INPUT_LIMIT, OUTPUT_LIMIT)
        if self._halt_reason:
            result["error"] = self._halt_reason
            return
        if self._remaining < reservation:
            result["error"] = "Insufficient remaining estimated budget for another dispatch."
            return

        body = request_body(case, prompt)
        body_bytes = json.dumps(body, ensure_ascii=False).encode("utf-8")
        if len(body_bytes) > REQUEST_BYTE_LIMIT:
            result["error"] = "Request exceeded the preflight size limit."
            return

        count_fields = ("messages", "model", "output_config", "system", "thinking")
        count_request = {key: body[key] for key in count_fields}
        timeout = self._request_timeout(result)
        if timeout is None:
            return
        try:
            count = self._client.messages.count_tokens(**count_request, timeout=timeout)
        except self._errors as exc:
            self._request_failed(exc, "Token-count preflight", result)
            return

        estimated_input = _token_count(getattr(count, "input_tokens", None))
        if estimated_input is None:
            result["error"] = "Token-count preflight lacked a valid token count."
            self._halt_reason = "Dispatch stopped after a malformed token-count response."
            return
        if estimated_input > PREFLIGHT_INPUT_LIMIT:
            result["error"] = "Token-count preflight exceeded the input limit."
            return

        timeout = self._request_timeout(result)
        if timeout is None:
            return

        # Unknown billing (including a timeout after dispatch) retains this reservation.
        self._remaining -= reservation
        result["cost_usd"] = None
        try:
            raw = self._client.messages.with_raw_response.create(**body, timeout=timeout)
            result["request_id"] = raw.request_id
            message = raw.parse()
        except self._errors as exc:
            self._request_failed(exc, "Messages request", result)
            return

        stop = getattr(message, "stop_reason", None)
        if isinstance(stop, str):
            result["stop_reason"] = stop
        model = getattr(message, "model", None)
        if isinstance(model, str) and model:
            result["model"] = model
        usage = getattr(message, "usage", None)
        reported_geo = getattr(usage, "inference_geo", None)
        if isinstance(reported_geo, str):
            result["diagnostics"] = {"inference_geo": reported_geo}
        input_count, output_count, cost, bounded = _reported_usage(usage)
        result["input_tokens"] = input_count
        result["output_tokens"] = output_count
        if model == MODEL and cost is not None:
            result["cost_usd"] = format(cost, "f")
            self._remaining += reservation - cost
        if model != MODEL or not bounded:
            self._halt_reason = (
                "Dispatch stopped after unexpected provider usage or model identity."
            )
            result["error"] = "Messages response had unexpected model or billable usage."
            return

        content = getattr(message, "content", None)
        if not isinstance(content, list):
            result["error"] = "Messages response lacked a recognized content list."
            self._halt_reason = "Dispatch stopped after a malformed Messages response."
            return
        result["text"] = "".join(
            block.text
            for block in content
            if getattr(block, "type", None) == "text"
            and isinstance(getattr(block, "text", None), str)
        )
        if isinstance(stop, str) and stop in UNFINISHED_STOPS:
            result["status"] = "invalid"
            result["error"] = f"Messages output stopped with {stop!r}."
        elif stop != "end_turn":
            result["error"] = "Messages response lacked a recognized stop reason."
            self._halt_reason = "Dispatch stopped after a malformed Messages response."
            return
        elif not result["text"].strip():
            result["status"] = "invalid"
            result["error"] = "Messages output contained no answer text."
        else:
            result["status"] = "ok"
        self._consecutive_transient_errors = 0

    def _request_timeout(self, result: dict) -> float | None:
        remaining = self._deadline - monotonic()
        if remaining <= 0:
            result["error"] = "Live comparison reached its run deadline."
            self._halt_reason = result["error"]
            return None
        return min(TIMEOUT_SECONDS, remaining)

    def _request_failed(self, error: Exception, operation: str, result: dict) -> None:
        error_type = type(error).__name__
        result["error"] = f"{operation} failed ({error_type})."
        result["request_id"] = getattr(error, "request_id", None) or result["request_id"]
        status = getattr(error, "status_code", None) or 0
        transient = (
            isinstance(error, self._connection_error) or status in (408, 409) or status >= 500
        )
        if not transient:
            self._halt_reason = f"Dispatch stopped after a fatal provider error ({error_type})."
            return

        self._consecutive_transient_errors += 1
        if self._consecutive_transient_errors >= TRANSIENT_FAILURE_LIMIT:
            self._halt_reason = "Dispatch stopped after successive transient provider errors."
