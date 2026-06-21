"""Official Python client for the Snipget API.

Snipget is a hosted utility API for AI agents and developers: data
normalization, parsing, validation, and classification. This package is a
thin HTTP wrapper around it — the per-endpoint contract lives in the
OpenAPI spec at https://api.snipget.ai/openapi.json.

    from snipget import Client

    client = Client(api_key="...")  # or set SNIPGET_API_KEY
    resp = client.call("/healthcare/npi/validate", {"npi": "1234567893"})
    print(resp.result, resp.confidence, resp.meta.request_id)
"""

from snipget._client import AsyncClient, Client
from snipget._exceptions import (
    APIError,
    AuthenticationError,
    InvalidRequestError,
    MaintenanceError,
    QuotaExceededError,
    RateLimitError,
    SnipgetError,
    UpstreamError,
    UpstreamRateLimitedError,
)
from snipget._response import ResponseMeta, SnipgetResponse
from snipget._version import __version__

__all__ = [
    "APIError",
    "AsyncClient",
    "AuthenticationError",
    "Client",
    "InvalidRequestError",
    "MaintenanceError",
    "QuotaExceededError",
    "RateLimitError",
    "ResponseMeta",
    "SnipgetError",
    "SnipgetResponse",
    "UpstreamError",
    "UpstreamRateLimitedError",
    "__version__",
]
