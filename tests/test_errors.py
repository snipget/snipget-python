"""Error-envelope to typed-exception mapping.

Each test pins one branch of the taxonomy so a mapping regression fails
the specific test for that error class. max_retries=0 keeps mapping tests
free of retry behavior (covered in test_retry.py).
"""

from __future__ import annotations

import httpx
import pytest
from _helpers import error_envelope, make_client, respond_json

import snipget

PATH = "/healthcare/npi/validate"
PAYLOAD = {"npi": "1234567893"}


def _client_returning(status_code: int, body: dict, **response_kwargs) -> snipget.Client:
    return make_client(
        lambda request: respond_json(body, status_code, **response_kwargs),
        max_retries=0,
    )


def test_401_missing_api_key_maps_to_authentication_error():
    client = _client_returning(401, error_envelope("MISSING_API_KEY", "No API key provided."))

    with pytest.raises(snipget.AuthenticationError) as exc_info:
        client.call(PATH, PAYLOAD)

    err = exc_info.value
    assert err.error_code == "MISSING_API_KEY"
    assert err.http_status == 401
    assert err.request_id == "req_err456"
    assert err.message == "No API key provided."


def test_401_invalid_api_key_maps_to_authentication_error():
    client = _client_returning(401, error_envelope("INVALID_API_KEY"))

    with pytest.raises(snipget.AuthenticationError) as exc_info:
        client.call(PATH, PAYLOAD)

    assert exc_info.value.error_code == "INVALID_API_KEY"


def test_403_ip_not_allowed_maps_to_authentication_error():
    """403 IP allowlist rejection is a credential/config problem, not a
    request problem — it belongs with the auth errors."""
    client = _client_returning(403, error_envelope("IP_NOT_ALLOWED"))

    with pytest.raises(snipget.AuthenticationError) as exc_info:
        client.call(PATH, PAYLOAD)

    assert exc_info.value.error_code == "IP_NOT_ALLOWED"
    assert exc_info.value.http_status == 403


def test_400_invalid_input_maps_to_invalid_request_error():
    client = _client_returning(400, error_envelope("INVALID_INPUT", "npi must be 10 digits."))

    with pytest.raises(snipget.InvalidRequestError) as exc_info:
        client.call(PATH, PAYLOAD)

    assert exc_info.value.error_code == "INVALID_INPUT"
    assert exc_info.value.http_status == 400


def test_422_validation_error_carries_details_in_body():
    """422 INVALID_REQUEST ships a Pydantic `details` list; the typed
    exception must keep it reachable via .body."""
    details = [{"loc": ["body", "npi"], "msg": "Field required", "type": "missing"}]
    client = _client_returning(
        422,
        error_envelope("INVALID_REQUEST", "Request validation failed.", details=details),
    )

    with pytest.raises(snipget.InvalidRequestError) as exc_info:
        client.call(PATH, {})

    assert exc_info.value.error_code == "INVALID_REQUEST"
    assert exc_info.value.body["details"] == details


def test_429_rate_limited_maps_with_retry_after_from_envelope():
    """The envelope's retry_after_seconds (exact float) wins over the
    Retry-After header (server-side it's rounded up to whole seconds)."""
    body = error_envelope(
        "RATE_LIMITED",
        "Too many requests.",
        retry_after_seconds=0.4,
        limit_type="sustained_rps",
        limit_value=10,
        current_tier="starter",
    )
    client = _client_returning(429, body, headers={"Retry-After": "1"})

    with pytest.raises(snipget.RateLimitError) as exc_info:
        client.call(PATH, PAYLOAD)

    err = exc_info.value
    assert err.error_code == "RATE_LIMITED"
    assert err.retry_after == 0.4
    assert err.body["limit_type"] == "sustained_rps"


def test_429_rate_limited_falls_back_to_retry_after_header():
    client = _client_returning(429, error_envelope("RATE_LIMITED"), headers={"Retry-After": "2"})

    with pytest.raises(snipget.RateLimitError) as exc_info:
        client.call(PATH, PAYLOAD)

    assert exc_info.value.retry_after == 2.0


def test_429_quota_exceeded_maps_to_quota_error_not_rate_limit():
    """QUOTA_EXCEEDED shares the 429 status with RATE_LIMITED but means
    something completely different (monthly capacity, not throttle) — it
    must come out as its own type so callers don't sleep-and-retry it."""
    body = error_envelope(
        "QUOTA_EXCEEDED",
        "Monthly call quota exceeded.",
        meta={"quota_remaining": 0, "credit_remaining_usd": 0.0},
        limit_type="included_exhausted",
        limit_value=25000,
        current_tier="starter",
    )
    client = _client_returning(429, body)

    with pytest.raises(snipget.QuotaExceededError) as exc_info:
        client.call(PATH, PAYLOAD)

    err = exc_info.value
    assert not isinstance(err, snipget.RateLimitError)
    assert err.error_code == "QUOTA_EXCEEDED"
    assert err.credit_remaining_usd == 0.0
    assert err.body["limit_type"] == "included_exhausted"


def test_quota_exceeded_without_credit_field():
    """Free-tier hard cap sends no allowance balance; attribute is None."""
    client = _client_returning(429, error_envelope("QUOTA_EXCEEDED", limit_type="monthly_quota"))

    with pytest.raises(snipget.QuotaExceededError) as exc_info:
        client.call(PATH, PAYLOAD)

    assert exc_info.value.credit_remaining_usd is None


def test_503_maintenance_mode_maps_with_retry_after():
    """Maintenance windows advertise Retry-After: 300; the exception
    carries it so callers can schedule their own retry."""
    body = error_envelope(
        "MAINTENANCE_MODE",
        "API is temporarily under maintenance. Try again in a few minutes.",
        retry_after_seconds=300,
    )
    client = _client_returning(503, body, headers={"Retry-After": "300"})

    with pytest.raises(snipget.MaintenanceError) as exc_info:
        client.call(PATH, PAYLOAD)

    assert exc_info.value.error_code == "MAINTENANCE_MODE"
    assert exc_info.value.retry_after == 300.0


def test_503_other_maps_to_plain_api_error():
    """A non-maintenance, non-upstream 503 (e.g. BILLING_UNAVAILABLE) is the
    generic APIError, not a more specific subclass."""
    client = _client_returning(503, error_envelope("BILLING_UNAVAILABLE"))

    with pytest.raises(snipget.APIError) as exc_info:
        client.call(PATH, PAYLOAD)

    err = exc_info.value
    assert not isinstance(err, (snipget.MaintenanceError, snipget.UpstreamError))
    assert err.error_code == "BILLING_UNAVAILABLE"


def test_503_upstream_unavailable_maps_to_upstream_error():
    """An external data source being down is UPSTREAM_UNAVAILABLE → UpstreamError
    (a subclass of APIError, so `except APIError` still catches it)."""
    client = _client_returning(503, error_envelope("UPSTREAM_UNAVAILABLE"))

    with pytest.raises(snipget.UpstreamError) as exc_info:
        client.call(PATH, PAYLOAD)

    err = exc_info.value
    assert isinstance(err, snipget.APIError)  # backward-compatible catch
    assert not isinstance(err, snipget.UpstreamRateLimitedError)
    assert err.error_code == "UPSTREAM_UNAVAILABLE"


def test_503_upstream_rate_limited_maps_with_retry_after():
    """An external source throttling us is UPSTREAM_RATE_LIMITED →
    UpstreamRateLimitedError carrying the upstream's Retry-After hint —
    distinct from our own RateLimitError (the caller's rate is fine)."""
    body = error_envelope(
        "UPSTREAM_RATE_LIMITED",
        "RxNorm is rate-limiting requests right now.",
        retry_after_seconds=5,
    )
    client = _client_returning(503, body, headers={"Retry-After": "5"})

    with pytest.raises(snipget.UpstreamRateLimitedError) as exc_info:
        client.call(PATH, PAYLOAD)

    err = exc_info.value
    assert isinstance(err, snipget.UpstreamError)  # and therefore APIError
    assert not isinstance(err, snipget.RateLimitError)  # NOT our own throttle
    assert err.error_code == "UPSTREAM_RATE_LIMITED"
    assert err.retry_after == 5.0


def test_500_internal_error_maps_to_api_error():
    client = _client_returning(500, error_envelope("INTERNAL_ERROR"))

    with pytest.raises(snipget.APIError) as exc_info:
        client.call(PATH, PAYLOAD)

    assert exc_info.value.error_code == "INTERNAL_ERROR"
    assert exc_info.value.http_status == 500


def test_404_maps_to_api_error():
    """Unknown paths get the envelope's HTTP_404 code; not a client bug
    class we can name better, so it lands in the APIError bucket."""
    client = _client_returning(404, error_envelope("HTTP_404", "Not Found"))

    with pytest.raises(snipget.APIError) as exc_info:
        client.call("/no/such/endpoint", {})

    assert exc_info.value.error_code == "HTTP_404"


def test_non_json_error_body_maps_to_api_error():
    """A gateway 502 with an HTML body must still raise a typed error."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="<html>Bad Gateway</html>")

    client = make_client(handler, max_retries=0)

    with pytest.raises(snipget.APIError) as exc_info:
        client.call(PATH, PAYLOAD)

    assert exc_info.value.http_status == 502
    assert exc_info.value.error_code is None


def test_all_typed_errors_are_snipget_errors():
    """One except-clause must be able to catch everything the SDK raises."""
    for exc_type in (
        snipget.AuthenticationError,
        snipget.InvalidRequestError,
        snipget.RateLimitError,
        snipget.QuotaExceededError,
        snipget.MaintenanceError,
        snipget.APIError,
        snipget.UpstreamError,
        snipget.UpstreamRateLimitedError,
    ):
        assert issubclass(exc_type, snipget.SnipgetError)
