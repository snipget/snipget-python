"""Sync and async HTTP clients for the Snipget API.

This module is deliberately a *thin* transport wrapper: it injects auth
headers, retries transient failures, and converts the JSON response
envelope into :class:`SnipgetResponse` / typed exceptions. It contains
zero business logic by design — the hosted API is the product, and the
OpenAPI spec at https://api.snipget.ai/openapi.json is the per-endpoint
contract.
"""

from __future__ import annotations

import asyncio
import os
import random
import time
from types import TracebackType
from typing import Any, Literal

import httpx

from snipget._exceptions import (
    APIError,
    AuthenticationError,
    InvalidRequestError,
    MaintenanceError,
    QuotaExceededError,
    RateLimitError,
    SnipgetError,
)
from snipget._response import SnipgetResponse
from snipget._version import __version__

__all__ = ["AsyncClient", "Client"]

DEFAULT_BASE_URL = "https://api.snipget.ai"
DEFAULT_TIMEOUT = 30.0
DEFAULT_MAX_RETRIES = 2

_ENV_API_KEY = "SNIPGET_API_KEY"
_USER_AGENT = f"snipget-python/{__version__}"

# Retry tuning. Exponential backoff: 0.5s, 1s, 2s, ... capped at 8s, plus
# up to 25% jitter so synchronized callers don't stampede on recovery.
_BACKOFF_BASE = 0.5
_BACKOFF_CAP = 8.0
# Never sleep longer than this even if the server's Retry-After asks for
# more (e.g. the 300s maintenance window) — a blocking 5-minute sleep
# inside a utility call would be hostile to callers.
_RETRY_AFTER_CAP = 60.0

# Test seam: tests monkeypatch these to assert on sleep behavior without
# slowing the suite down.
_sleep = time.sleep
_async_sleep = asyncio.sleep

AuthHeaderStyle = Literal["authorization", "x-api-key"]


def _resolve_api_key(api_key: str | None) -> str:
    key = api_key if api_key is not None else os.environ.get(_ENV_API_KEY)
    if not key:
        raise AuthenticationError(
            "No API key provided. Pass api_key=... to the client or set the "
            f"{_ENV_API_KEY} environment variable. Get a key at https://snipget.ai."
        )
    return key


def _build_headers(api_key: str, auth_header: AuthHeaderStyle) -> dict[str, str]:
    headers = {"User-Agent": _USER_AGENT}
    if auth_header == "authorization":
        headers["Authorization"] = f"Bearer {api_key}"
    elif auth_header == "x-api-key":
        headers["X-API-Key"] = api_key
    else:
        raise ValueError(f"auth_header must be 'authorization' or 'x-api-key', got {auth_header!r}")
    return headers


def _resolve_method(method: str | None, payload: dict[str, Any] | None) -> str:
    """Default to POST when a payload is given, GET otherwise.

    All 128 utility endpoints are POST; the handful of GET endpoints
    (/, /health, /pricing/tiers, ...) take no payload, so the default does
    the right thing for every path in the spec. ``method`` overrides.
    """
    if method is not None:
        return method.upper()
    return "POST" if payload is not None else "GET"


def _normalize_path(path: str) -> str:
    return path if path.startswith("/") else f"/{path}"


def _retry_after_seconds(response: httpx.Response, body: dict[str, Any] | None) -> float | None:
    """Pull the retry hint from ``retry_after_seconds`` (envelope, exact
    float) or the ``Retry-After`` header (integer seconds, rounded up by
    the server)."""
    if isinstance(body, dict):
        value = body.get("retry_after_seconds")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    header = response.headers.get("Retry-After")
    if header is not None:
        try:
            return float(header)
        except ValueError:
            return None
    return None


def _parse_success(response: httpx.Response) -> SnipgetResponse:
    try:
        data = response.json()
    except ValueError as exc:
        raise APIError(
            "Expected a JSON envelope but the response body was not valid JSON.",
            http_status=response.status_code,
        ) from exc
    if not isinstance(data, dict):
        raise APIError(
            "Expected a JSON envelope object but got a different JSON type.",
            http_status=response.status_code,
        )
    return SnipgetResponse.from_dict(data)


def _error_from_response(response: httpx.Response) -> SnipgetError:
    """Map a non-2xx response onto the typed exception taxonomy."""
    status = response.status_code
    try:
        body = response.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        return APIError(
            f"HTTP {status} with a non-JSON body.",
            http_status=status,
        )

    error_code = body.get("error_code")
    message = body.get("message") or f"HTTP {status}"
    meta = body.get("meta") if isinstance(body.get("meta"), dict) else {}
    common: dict[str, Any] = {
        "error_code": error_code,
        "request_id": meta.get("request_id"),
        "http_status": status,
        "body": body,
    }

    if status == 429:
        if error_code == "QUOTA_EXCEEDED":
            return QuotaExceededError(
                message,
                credit_remaining_usd=meta.get("credit_remaining_usd"),
                **common,
            )
        # RATE_LIMITED (and any future throttle variant on 429).
        return RateLimitError(
            message,
            retry_after=_retry_after_seconds(response, body),
            **common,
        )
    if status == 503 and error_code == "MAINTENANCE_MODE":
        return MaintenanceError(
            message,
            retry_after=_retry_after_seconds(response, body),
            **common,
        )
    if status in (401, 403):
        return AuthenticationError(message, **common)
    if status in (400, 422):
        return InvalidRequestError(message, **common)
    return APIError(message, **common)


def _is_retryable(error: SnipgetError) -> bool:
    """Whether a retry can possibly succeed.

    Snipget utility calls are pure and idempotent (same input, same
    output, no server-side state mutation), so retrying POSTs is safe.

    - QUOTA_EXCEEDED never retries: it doesn't lift until the monthly
      reset, a tier upgrade, or an allowance top-up.
    - RATE_LIMITED retries: it's a per-second throttle.
    - 5xx retries (includes MAINTENANCE_MODE, with short backoff).
    - All other 4xx never retry: resending the same bad request can't help.
    """
    if isinstance(error, QuotaExceededError):
        return False
    if isinstance(error, RateLimitError):
        return True
    return error.http_status is not None and error.http_status >= 500


def _retry_delay(error: SnipgetError | None, attempt: int) -> float:
    """Seconds to sleep before retry number ``attempt`` (0-based).

    RATE_LIMITED honors the server's Retry-After (capped). Everything
    else — network errors, 5xx, maintenance — uses exponential backoff
    with jitter; we deliberately do NOT honor maintenance's 300s hint
    here (see MaintenanceError's docstring).
    """
    if isinstance(error, RateLimitError) and error.retry_after is not None:
        return min(error.retry_after, _RETRY_AFTER_CAP)
    base = min(_BACKOFF_BASE * (2**attempt), _BACKOFF_CAP)
    return base + random.uniform(0.0, base / 4)


class Client:
    """Synchronous Snipget API client.

    Args:
        api_key: Your Snipget API key. Falls back to the
            ``SNIPGET_API_KEY`` environment variable when omitted.
        base_url: API origin; override for testing or self-hosted stacks.
        timeout: Per-request timeout in seconds.
        max_retries: How many times to retry retryable failures (network
            errors, RATE_LIMITED, 5xx) on top of the initial attempt.
        auth_header: ``"authorization"`` sends ``Authorization: Bearer <key>``
            (preferred); ``"x-api-key"`` sends ``X-API-Key: <key>``.
        transport: Optional httpx transport (proxies, mocking, ...).

    Usage:
        >>> from snipget import Client
        >>> client = Client()  # reads SNIPGET_API_KEY
        >>> resp = client.call("/healthcare/npi/validate", {"npi": "1234567893"})
        >>> resp.result["is_valid"]
        True
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        auth_header: AuthHeaderStyle = "authorization",
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.api_key = _resolve_api_key(api_key)
        self.base_url = base_url.rstrip("/")
        self.max_retries = max_retries
        self._http = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            headers=_build_headers(self.api_key, auth_header),
            transport=transport,
        )

    def call(
        self,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        method: str | None = None,
    ) -> SnipgetResponse:
        """Call any Snipget endpoint and return the parsed envelope.

        Args:
            path: Endpoint path, e.g. ``"/healthcare/npi/validate"``.
            payload: JSON body. When given, the request defaults to POST.
            method: Explicit HTTP method override.

        Raises:
            SnipgetError subclasses mapped from the error envelope.
        """
        resolved_method = _resolve_method(method, payload)
        request_path = _normalize_path(path)
        for attempt in range(self.max_retries + 1):
            try:
                response = self._http.request(resolved_method, request_path, json=payload)
            except httpx.TransportError as exc:
                if attempt >= self.max_retries:
                    raise APIError(f"Network error calling {request_path}: {exc}") from exc
                _sleep(_retry_delay(None, attempt))
                continue
            if response.is_success:
                return _parse_success(response)
            error = _error_from_response(response)
            if attempt >= self.max_retries or not _is_retryable(error):
                raise error
            _sleep(_retry_delay(error, attempt))
        raise AssertionError("unreachable")  # pragma: no cover

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> Client:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


class AsyncClient:
    """Asynchronous Snipget API client. Same surface as :class:`Client`.

    Usage:
        >>> from snipget import AsyncClient
        >>> async with AsyncClient() as client:
        ...     resp = await client.call("/healthcare/npi/validate", {"npi": "1234567893"})
        ...     resp.result["is_valid"]
        True
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        auth_header: AuthHeaderStyle = "authorization",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.api_key = _resolve_api_key(api_key)
        self.base_url = base_url.rstrip("/")
        self.max_retries = max_retries
        self._http = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout,
            headers=_build_headers(self.api_key, auth_header),
            transport=transport,
        )

    async def call(
        self,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        method: str | None = None,
    ) -> SnipgetResponse:
        """Async variant of :meth:`Client.call`."""
        resolved_method = _resolve_method(method, payload)
        request_path = _normalize_path(path)
        for attempt in range(self.max_retries + 1):
            try:
                response = await self._http.request(resolved_method, request_path, json=payload)
            except httpx.TransportError as exc:
                if attempt >= self.max_retries:
                    raise APIError(f"Network error calling {request_path}: {exc}") from exc
                await _async_sleep(_retry_delay(None, attempt))
                continue
            if response.is_success:
                return _parse_success(response)
            error = _error_from_response(response)
            if attempt >= self.max_retries or not _is_retryable(error):
                raise error
            await _async_sleep(_retry_delay(error, attempt))
        raise AssertionError("unreachable")  # pragma: no cover

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> AsyncClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()
