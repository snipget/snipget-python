"""Typed exception taxonomy for the Snipget client.

Every exception maps one-to-one onto the API's error envelope:

    {"status": "error", "error_code": "...", "message": "...", "meta": {...}}

The full parsed envelope is always available on ``exc.body`` so callers can
reach envelope fields the typed attributes don't surface (e.g. the ``details``
list on 422 validation errors, or ``limit_type`` on quota errors).
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "APIError",
    "AuthenticationError",
    "InvalidRequestError",
    "MaintenanceError",
    "QuotaExceededError",
    "RateLimitError",
    "SnipgetError",
    "UpstreamError",
    "UpstreamRateLimitedError",
]


class SnipgetError(Exception):
    """Base class for every error raised by the Snipget client.

    Attributes:
        message: Human-readable message from the API (or the client).
        error_code: Machine-readable code from the error envelope,
            e.g. ``"INVALID_API_KEY"``. ``None`` when no envelope was
            available (network failure, non-JSON body).
        request_id: The ``meta.request_id`` from the envelope; quote it
            when contacting support.
        http_status: HTTP status code of the response, or ``None`` for
            errors raised before a response existed.
        body: The full parsed error envelope dict, when available.
    """

    def __init__(
        self,
        message: str,
        *,
        error_code: str | None = None,
        request_id: str | None = None,
        http_status: int | None = None,
        body: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.request_id = request_id
        self.http_status = http_status
        self.body = body

    def __str__(self) -> str:
        parts = [self.message]
        if self.error_code is not None:
            parts.append(f"[error_code={self.error_code}]")
        if self.http_status is not None:
            parts.append(f"[http_status={self.http_status}]")
        if self.request_id is not None:
            parts.append(f"[request_id={self.request_id}]")
        return " ".join(parts)


class AuthenticationError(SnipgetError):
    """401/403 — missing, invalid, or restricted API key.

    Covers ``MISSING_API_KEY``, ``INVALID_API_KEY``, and ``IP_NOT_ALLOWED``.
    Also raised client-side when no API key can be resolved at all.
    """


class InvalidRequestError(SnipgetError):
    """400/422 — the request was rejected before any work was done.

    ``INVALID_INPUT`` (400) or ``INVALID_REQUEST`` (422). For 422s the
    Pydantic field errors are in ``exc.body["details"]``.
    """


class RateLimitError(SnipgetError):
    """429 ``RATE_LIMITED`` — per-second throughput throttle.

    Retryable within seconds; the client retries these automatically,
    honoring ``retry_after``. Distinct from :class:`QuotaExceededError`,
    which does not lift until the next month / an upgrade / a top-up.

    Attributes:
        retry_after: Seconds to wait before retrying, from the envelope's
            ``retry_after_seconds`` (preferred) or the ``Retry-After``
            header. ``None`` if the server sent neither.
    """

    def __init__(self, message: str, *, retry_after: float | None = None, **kwargs: Any) -> None:
        super().__init__(message, **kwargs)
        self.retry_after = retry_after


class QuotaExceededError(SnipgetError):
    """429 ``QUOTA_EXCEEDED`` — monthly quota or prepaid allowance exhausted.

    NOT retryable: it does not lift until the next UTC calendar month, a
    tier upgrade, or an allowance purchase. The client never retries it.
    ``exc.body["limit_type"]`` says which recovery applies
    (``monthly_quota`` / ``included_exhausted`` / ``overage_balance_exhausted``).

    Attributes:
        credit_remaining_usd: Live prepaid-allowance balance from
            ``meta.credit_remaining_usd``, when the server included it.
    """

    def __init__(
        self,
        message: str,
        *,
        credit_remaining_usd: float | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(message, **kwargs)
        self.credit_remaining_usd = credit_remaining_usd


class MaintenanceError(SnipgetError):
    """503 ``MAINTENANCE_MODE`` — the API is in a maintenance window.

    Attributes:
        retry_after: Seconds until the server suggests retrying
            (typically 300). The client's automatic retries use its own
            short backoff instead of sleeping this long; if you see this
            exception, wait ``retry_after`` seconds and call again.
    """

    def __init__(self, message: str, *, retry_after: float | None = None, **kwargs: Any) -> None:
        super().__init__(message, **kwargs)
        self.retry_after = retry_after


class APIError(SnipgetError):
    """Any other failure: unexpected status codes, 5xx errors, non-JSON
    bodies, and network errors that survived the retry budget
    (``http_status`` is ``None`` for those)."""


class UpstreamError(APIError):
    """503 ``UPSTREAM_UNAVAILABLE`` — an external data source a utility
    depends on (PubChem, RxNorm, ClinicalTrials.gov, the FX feed, NPPES…)
    is down, blocking us, or timed out.

    A subclass of :class:`APIError` (it *is* a 5xx), so existing
    ``except APIError`` handlers keep catching it. Transient and
    retryable; the client retries it automatically on the short backoff.
    """


class UpstreamRateLimitedError(UpstreamError):
    """503 ``UPSTREAM_RATE_LIMITED`` — an external data source is throttling
    Snipget (it returned a 429). The caller's own request rate is fine; this
    is a service-side throttle, distinct from :class:`RateLimitError`.

    Transient and retryable; the client retries it automatically and honors
    ``retry_after`` when the upstream supplied one.

    Attributes:
        retry_after: Seconds to wait before retrying, from the envelope's
            ``retry_after_seconds`` (preferred) or the ``Retry-After``
            header. ``None`` if the upstream sent neither.
    """

    def __init__(self, message: str, *, retry_after: float | None = None, **kwargs: Any) -> None:
        super().__init__(message, **kwargs)
        self.retry_after = retry_after
