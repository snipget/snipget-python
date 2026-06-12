"""AsyncClient parity: same envelope unwrapping, error taxonomy, retry
policy, and auth header handling as the sync client."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from _helpers import TEST_KEY, error_envelope, make_async_client, respond_json, success_envelope

import snipget

PATH = "/healthcare/npi/validate"
PAYLOAD = {"npi": "1234567893"}


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def async_sleeps(monkeypatch) -> list[float]:
    recorded: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        recorded.append(seconds)

    monkeypatch.setattr("snipget._client._async_sleep", fake_sleep)
    return recorded


def test_async_success_envelope_unwrap():
    body = success_envelope(result={"is_valid": True}, confidence=1.0)

    async def main():
        async with make_async_client(lambda request: respond_json(body)) as client:
            return await client.call(PATH, PAYLOAD)

    resp = run(main())

    assert resp.status == "ok"
    assert resp.result == {"is_valid": True}
    assert resp.meta.request_id == "req_test123"
    assert resp.raw == body


def test_async_error_mapping_authentication():
    async def main():
        client = make_async_client(
            lambda request: respond_json(error_envelope("INVALID_API_KEY"), 401),
            max_retries=0,
        )
        try:
            await client.call(PATH, PAYLOAD)
        finally:
            await client.aclose()

    with pytest.raises(snipget.AuthenticationError) as exc_info:
        run(main())

    assert exc_info.value.error_code == "INVALID_API_KEY"
    assert exc_info.value.http_status == 401


def test_async_quota_exceeded_never_retried(async_sleeps):
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return respond_json(
            error_envelope("QUOTA_EXCEEDED", meta={"credit_remaining_usd": 0.0}), 429
        )

    async def main():
        async with make_async_client(handler, max_retries=5) as client:
            await client.call(PATH, PAYLOAD)

    with pytest.raises(snipget.QuotaExceededError) as exc_info:
        run(main())

    assert len(calls) == 1
    assert async_sleeps == []
    assert exc_info.value.credit_remaining_usd == 0.0


def test_async_rate_limited_retried_honoring_retry_after(async_sleeps):
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return respond_json(error_envelope("RATE_LIMITED", retry_after_seconds=0.7), 429)
        return respond_json(success_envelope())

    async def main():
        async with make_async_client(handler) as client:
            return await client.call(PATH, PAYLOAD)

    resp = run(main())

    assert resp.status == "ok"
    assert len(calls) == 2
    assert async_sleeps == [0.7]


def test_async_network_error_retried_then_wrapped(async_sleeps):
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        raise httpx.ConnectError("connection refused")

    async def main():
        async with make_async_client(handler, max_retries=1) as client:
            await client.call(PATH, PAYLOAD)

    with pytest.raises(snipget.APIError, match="Network error"):
        run(main())

    assert len(calls) == 2


def test_async_auth_header_styles():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return respond_json(success_envelope())

    async def main():
        async with make_async_client(handler) as bearer_client:
            await bearer_client.call(PATH, PAYLOAD)
        async with make_async_client(handler, auth_header="x-api-key") as key_client:
            await key_client.call(PATH, PAYLOAD)

    run(main())

    assert seen[0].headers["Authorization"] == f"Bearer {TEST_KEY}"
    assert seen[1].headers["X-API-Key"] == TEST_KEY
    assert "Authorization" not in seen[1].headers


def test_async_env_var_key_fallback(monkeypatch):
    monkeypatch.setenv("SNIPGET_API_KEY", "sk_from_env")
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return respond_json(success_envelope())

    async def main():
        client = snipget.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            await client.call(PATH, PAYLOAD)
        finally:
            await client.aclose()

    run(main())

    assert seen[0].headers["Authorization"] == "Bearer sk_from_env"


def test_async_payload_defaults_to_post_no_payload_get():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return respond_json(success_envelope())

    async def main():
        async with make_async_client(handler) as client:
            await client.call(PATH, PAYLOAD)
            await client.call("/health")

    run(main())

    assert seen[0].method == "POST"
    assert seen[1].method == "GET"


def test_async_context_manager_closes_transport():
    async def main():
        async with make_async_client(lambda request: respond_json(success_envelope())) as client:
            await client.call(PATH, PAYLOAD)
        return client

    client = run(main())

    assert client._http.is_closed
