"""Shared test helpers: envelope builders and mock-transport client factories.

Envelope shapes mirror the live API contract (snipget-app's
``api/schemas.py`` + ``api/exceptions.py``): success/error envelopes,
the RATE_LIMITED 429 variant with top-level retry fields and a
``Retry-After`` header, and the QUOTA_EXCEEDED 429 variant.
"""

from __future__ import annotations

from typing import Any

import httpx

import snipget

TEST_KEY = "sk_test_abc123"


def success_envelope(
    result: Any = None,
    *,
    confidence: float = 1.0,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    base_meta: dict[str, Any] = {
        "version": "0.1.0",
        "elapsed_ms": 3,
        "cost_units": 1,
        "request_id": "req_test123",
    }
    if meta:
        base_meta.update(meta)
    return {
        "status": "ok",
        "confidence": confidence,
        "result": result if result is not None else {"ok": True},
        "meta": base_meta,
    }


def error_envelope(
    error_code: str,
    message: str = "boom",
    *,
    meta: dict[str, Any] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Error envelope. ``extra`` lands as top-level fields, mirroring the
    server's ``_build_error_response`` (retry_after_seconds, limit_type,
    details, ...)."""
    base_meta: dict[str, Any] = {
        "version": "0.1.0",
        "cost_units": 0,
        "request_id": "req_err456",
    }
    if meta:
        base_meta.update(meta)
    body: dict[str, Any] = {
        "status": "error",
        "error_code": error_code,
        "message": message,
        "meta": base_meta,
    }
    body.update(extra)
    return body


def make_client(handler, **kwargs) -> snipget.Client:
    kwargs.setdefault("api_key", TEST_KEY)
    return snipget.Client(transport=httpx.MockTransport(handler), **kwargs)


def make_async_client(handler, **kwargs) -> snipget.AsyncClient:
    kwargs.setdefault("api_key", TEST_KEY)
    return snipget.AsyncClient(transport=httpx.MockTransport(handler), **kwargs)


def respond_json(body: dict[str, Any], status_code: int = 200, **kwargs) -> httpx.Response:
    return httpx.Response(status_code, json=body, **kwargs)
