"""Client construction, auth header injection, request shaping, and
success-envelope unwrapping."""

from __future__ import annotations

import json

import httpx
import pytest
from _helpers import TEST_KEY, make_client, respond_json, success_envelope

import snipget

NPI_PATH = "/healthcare/npi/validate"


def test_success_envelope_unwrap():
    """The whole point of SnipgetResponse: result/confidence/meta come out
    of the envelope without callers touching raw JSON."""
    body = success_envelope(
        result={"npi": 1234567893, "is_valid": True, "checksum_valid": True},
        confidence=1.0,
    )
    client = make_client(lambda request: respond_json(body))

    resp = client.call(NPI_PATH, {"npi": "1234567893"})

    assert resp.status == "ok"
    assert resp.confidence == 1.0
    assert resp.result["is_valid"] is True
    assert resp.meta.cost_units == 1
    assert resp.meta.elapsed_ms == 3
    assert resp.meta.request_id == "req_test123"
    assert resp.raw == body


def test_meta_typed_fields():
    """Rate-limit, quota, and allowance headroom are the agent-pacing
    signals; they must surface as typed meta fields."""
    body = success_envelope(
        meta={
            "rate_limit_remaining": 9,
            "rate_limit_reset": 1765500000,
            "quota_remaining": 4900,
            "quota_reset": 1767225600,
            "credit_remaining_usd": 9.25,
        }
    )
    client = make_client(lambda request: respond_json(body))

    meta = client.call(NPI_PATH, {"npi": "1234567893"}).meta

    assert meta.rate_limit_remaining == 9
    assert meta.rate_limit_reset == 1765500000
    assert meta.quota_remaining == 4900
    assert meta.quota_reset == 1767225600
    assert meta.credit_remaining_usd == 9.25


def test_meta_unknown_keys_preserved_in_raw():
    """The server may add meta fields any time (extra='allow' on its
    side); the client must not drop them."""
    body = success_envelope(meta={"brand_new_field": "hello"})
    client = make_client(lambda request: respond_json(body))

    meta = client.call(NPI_PATH, {"npi": "1234567893"}).meta

    assert meta.raw["brand_new_field"] == "hello"


def test_batch_envelope_unwraps_like_single():
    """Batch endpoints share the envelope: top-level confidence is 1.0,
    per-item confidences live inside result.items."""
    body = success_envelope(
        result={
            "items": [
                {"input": "1234567893", "confidence": 1.0, "is_valid": True},
                {"input": "0000000000", "confidence": 0.0, "is_valid": False},
            ],
            "summary": {"total": 2, "valid": 1, "invalid": 1},
        },
        confidence=1.0,
        meta={"cost_units": 2},
    )
    client = make_client(lambda request: respond_json(body))

    resp = client.call("/healthcare/npi/validate/batch", {"items": ["1234567893", "0000000000"]})

    assert resp.confidence == 1.0
    assert len(resp.result["items"]) == 2
    assert resp.result["items"][1]["confidence"] == 0.0
    assert resp.meta.cost_units == 2


def test_payload_defaults_to_post_with_json_body():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return respond_json(success_envelope())

    make_client(handler).call(NPI_PATH, {"npi": "1234567893"})

    assert seen[0].method == "POST"
    assert json.loads(seen[0].content) == {"npi": "1234567893"}


def test_no_payload_defaults_to_get():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return respond_json(success_envelope())

    make_client(handler).call("/health")

    assert seen[0].method == "GET"


def test_explicit_method_overrides_default():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return respond_json(success_envelope())

    make_client(handler).call("/pricing/tiers", method="get")

    assert seen[0].method == "GET"


def test_path_without_leading_slash_is_normalized():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return respond_json(success_envelope())

    make_client(handler).call("healthcare/npi/validate", {"npi": "1234567893"})

    assert seen[0].url.path == "/healthcare/npi/validate"


def test_auth_header_bearer_default():
    """Authorization: Bearer is the preferred credential style."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return respond_json(success_envelope())

    make_client(handler).call(NPI_PATH, {"npi": "1"})

    assert seen[0].headers["Authorization"] == f"Bearer {TEST_KEY}"
    assert "X-API-Key" not in seen[0].headers


def test_auth_header_x_api_key_style():
    """The API also accepts X-API-Key; the client must support both."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return respond_json(success_envelope())

    make_client(handler, auth_header="x-api-key").call(NPI_PATH, {"npi": "1"})

    assert seen[0].headers["X-API-Key"] == TEST_KEY
    assert "Authorization" not in seen[0].headers


def test_invalid_auth_header_style_rejected():
    with pytest.raises(ValueError, match="auth_header"):
        make_client(lambda request: respond_json(success_envelope()), auth_header="cookie")


def test_user_agent_identifies_sdk():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return respond_json(success_envelope())

    make_client(handler).call(NPI_PATH, {"npi": "1"})

    assert seen[0].headers["User-Agent"] == f"snipget-python/{snipget.__version__}"


def test_env_var_key_fallback(monkeypatch):
    """SNIPGET_API_KEY is the zero-config path for agents and CI."""
    monkeypatch.setenv("SNIPGET_API_KEY", "sk_from_env")
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return respond_json(success_envelope())

    client = snipget.Client(transport=httpx.MockTransport(handler))
    client.call(NPI_PATH, {"npi": "1"})

    assert client.api_key == "sk_from_env"
    assert seen[0].headers["Authorization"] == "Bearer sk_from_env"


def test_explicit_key_beats_env_var(monkeypatch):
    monkeypatch.setenv("SNIPGET_API_KEY", "sk_from_env")

    client = snipget.Client(
        api_key="sk_explicit",
        transport=httpx.MockTransport(lambda request: respond_json(success_envelope())),
    )

    assert client.api_key == "sk_explicit"


def test_missing_key_raises_authentication_error(monkeypatch):
    """Failing at construction time beats failing on the first call."""
    monkeypatch.delenv("SNIPGET_API_KEY", raising=False)

    with pytest.raises(snipget.AuthenticationError, match="SNIPGET_API_KEY"):
        snipget.Client()


def test_context_manager_closes_transport():
    with make_client(lambda request: respond_json(success_envelope())) as client:
        client.call(NPI_PATH, {"npi": "1"})

    assert client._http.is_closed


def test_base_url_trailing_slash_tolerated():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return respond_json(success_envelope())

    client = make_client(handler, base_url="https://api.snipget.ai/")
    client.call(NPI_PATH, {"npi": "1"})

    assert str(seen[0].url) == "https://api.snipget.ai/healthcare/npi/validate"


def test_non_json_success_body_raises_api_error():
    """A 200 that isn't a JSON envelope is a broken proxy or captive
    portal — surface it loudly instead of returning garbage."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>hi</html>")

    with pytest.raises(snipget.APIError, match="not valid JSON"):
        make_client(handler).call(NPI_PATH, {"npi": "1"})
