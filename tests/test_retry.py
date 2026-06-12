"""Retry policy: what gets retried, what never does, and how long we wait.

POST retry is safe here because Snipget utility calls are pure and
idempotent — these tests pin that policy, not just the mechanics.
"""

from __future__ import annotations

import httpx
import pytest
from _helpers import error_envelope, make_client, respond_json, success_envelope

import snipget

PATH = "/healthcare/npi/validate"
PAYLOAD = {"npi": "1234567893"}


@pytest.fixture
def sleeps(monkeypatch) -> list[float]:
    """Capture retry sleeps instead of actually sleeping."""
    recorded: list[float] = []
    monkeypatch.setattr("snipget._client._sleep", recorded.append)
    return recorded


def test_rate_limited_then_success_is_retried_honoring_retry_after(sleeps):
    """RATE_LIMITED is a per-second throttle: wait what the server asked,
    then the retry succeeds."""
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return respond_json(
                error_envelope("RATE_LIMITED", retry_after_seconds=1.5),
                429,
                headers={"Retry-After": "2"},
            )
        return respond_json(success_envelope())

    resp = make_client(handler).call(PATH, PAYLOAD)

    assert resp.status == "ok"
    assert len(calls) == 2
    assert sleeps == [1.5]  # envelope value, not the rounded-up header


def test_quota_exceeded_is_never_retried(sleeps):
    """Retrying QUOTA_EXCEEDED can never succeed (monthly capacity);
    burning retries on it would just add latency and log noise."""
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return respond_json(error_envelope("QUOTA_EXCEEDED", limit_type="monthly_quota"), 429)

    with pytest.raises(snipget.QuotaExceededError):
        make_client(handler, max_retries=5).call(PATH, PAYLOAD)

    assert len(calls) == 1
    assert sleeps == []


def test_4xx_is_never_retried(sleeps):
    """Resending the same bad request can't fix it."""
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return respond_json(error_envelope("INVALID_INPUT"), 400)

    with pytest.raises(snipget.InvalidRequestError):
        make_client(handler, max_retries=5).call(PATH, PAYLOAD)

    assert len(calls) == 1
    assert sleeps == []


def test_500_then_success_is_retried(sleeps):
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return respond_json(error_envelope("INTERNAL_ERROR"), 500)
        return respond_json(success_envelope())

    resp = make_client(handler).call(PATH, PAYLOAD)

    assert resp.status == "ok"
    assert len(calls) == 2
    assert len(sleeps) == 1


def test_network_error_then_success_is_retried(sleeps):
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ConnectError("connection refused")
        return respond_json(success_envelope())

    resp = make_client(handler).call(PATH, PAYLOAD)

    assert resp.status == "ok"
    assert len(calls) == 2


def test_retries_exhausted_raises_last_rate_limit_error(sleeps):
    """max_retries bounds the budget: initial attempt + N retries, then
    the final typed error propagates."""
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return respond_json(error_envelope("RATE_LIMITED", retry_after_seconds=1.0), 429)

    with pytest.raises(snipget.RateLimitError):
        make_client(handler, max_retries=2).call(PATH, PAYLOAD)

    assert len(calls) == 3
    assert sleeps == [1.0, 1.0]


def test_network_errors_exhausted_raise_api_error(sleeps):
    """Callers should only ever need `except SnipgetError`; raw httpx
    transport errors must not leak out of call()."""
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        raise httpx.ConnectError("connection refused")

    with pytest.raises(snipget.APIError, match="Network error") as exc_info:
        make_client(handler, max_retries=2).call(PATH, PAYLOAD)

    assert len(calls) == 3
    assert exc_info.value.http_status is None
    assert isinstance(exc_info.value.__cause__, httpx.ConnectError)


def test_max_retries_zero_disables_retries(sleeps):
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return respond_json(error_envelope("INTERNAL_ERROR"), 500)

    with pytest.raises(snipget.APIError):
        make_client(handler, max_retries=0).call(PATH, PAYLOAD)

    assert len(calls) == 1
    assert sleeps == []


def test_maintenance_retries_use_backoff_not_the_300s_hint(sleeps):
    """Deliberate policy: a maintenance 503 advertises Retry-After: 300,
    but sleeping 5 minutes inside call() would be hostile. We retry on
    the normal short backoff and, if it's still down, raise
    MaintenanceError carrying retry_after for the caller to schedule."""
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return respond_json(
            error_envelope("MAINTENANCE_MODE", retry_after_seconds=300),
            503,
            headers={"Retry-After": "300"},
        )

    with pytest.raises(snipget.MaintenanceError) as exc_info:
        make_client(handler, max_retries=1).call(PATH, PAYLOAD)

    assert len(calls) == 2
    assert all(s < 60 for s in sleeps)
    assert exc_info.value.retry_after == 300.0


def test_huge_retry_after_is_capped(sleeps):
    """Defensive cap: never let a server-supplied Retry-After park the
    caller for minutes."""
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return respond_json(error_envelope("RATE_LIMITED", retry_after_seconds=9999), 429)
        return respond_json(success_envelope())

    make_client(handler).call(PATH, PAYLOAD)

    assert sleeps == [60.0]


def test_backoff_grows_exponentially(sleeps):
    """Successive retry delays must grow (0.5s-ish then 1s-ish), so a
    struggling server gets breathing room."""
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return respond_json(error_envelope("INTERNAL_ERROR"), 500)

    with pytest.raises(snipget.APIError):
        make_client(handler, max_retries=2).call(PATH, PAYLOAD)

    assert len(sleeps) == 2
    assert 0.5 <= sleeps[0] <= 0.625  # base 0.5 + up to 25% jitter
    assert 1.0 <= sleeps[1] <= 1.25
    assert sleeps[1] > sleeps[0]
