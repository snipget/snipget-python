# snipget-client

The official Python client for [Snipget](https://snipget.ai), the hosted utility API for AI agents: data normalization, parsing, validation, and classification over plain HTTPS.

## What is Snipget

Snipget is a hosted, pay-per-call utility API built for AI agents and the developers who build them. It serves 130+ programmatic endpoints for data normalization, parsing, validation, and classification, with particular depth in healthcare data: NPI validation and lookup, DEA numbers, provider taxonomy, credentials, and certifications. Every endpoint is deterministic (no LLM calls inside the API), returns a confidence score, and ships in single-record and batch variants.

Snipget is agent-native by design. Agents can discover and call it through the [OpenAPI spec](https://api.snipget.ai/openapi.json) or the MCP server, and every response uses one consistent JSON envelope so a single integration covers the whole catalog. This package is a thin HTTP wrapper around that hosted API; all the actual logic runs server-side, and the [interactive docs](https://api.snipget.ai/docs) are the per-endpoint contract.

## Install

```bash
pip install snipget-client
```

Requires Python 3.10+. The only dependency is [httpx](https://www.python-httpx.org/).

## Quickstart

You need an API key from [snipget.ai](https://snipget.ai). One generic `call()` method reaches every endpoint; pass the path and the JSON payload from the [API docs](https://api.snipget.ai/docs).

```python
from snipget import Client

client = Client(api_key="YOUR_API_KEY")  # or set SNIPGET_API_KEY

resp = client.call("/healthcare/npi/validate", {"npi": "1234567893"})

print(resp.result)
# {'npi': 1234567893, 'is_valid': True, 'checksum_valid': True, 'input_was_clean': True}
print(resp.confidence)        # 1.0
print(resp.meta.cost_units)   # 1
print(resp.meta.request_id)   # 'req_...'

# Batch variants exist for every utility:
resp = client.call(
    "/healthcare/npi/validate/batch",
    {"items": ["1234567893", "1234567890"]},
)
print(resp.result["summary"])  # {'total': 2, 'valid': 1, 'invalid': 1}
```

Async, same surface:

```python
import asyncio
from snipget import AsyncClient

async def main():
    async with AsyncClient() as client:  # reads SNIPGET_API_KEY
        resp = await client.call(
            "/common/phone/validate",
            {"value": "(415) 555-0132", "country_hint": "US"},
        )
        print(resp.result)

asyncio.run(main())
```

`call()` defaults to `POST` when a payload is given and `GET` otherwise, which matches every endpoint in the spec; pass `method=` to override.

## Authentication

Get an API key at [snipget.ai](https://snipget.ai). The client resolves the key in this order:

1. `Client(api_key="...")`
2. The `SNIPGET_API_KEY` environment variable

By default the key is sent as `Authorization: Bearer <key>`. The API also accepts an `X-API-Key` header; opt in with `Client(auth_header="x-api-key")`.

## Error handling

Every API error is raised as a typed exception. All of them subclass `SnipgetError` and carry `error_code`, `message`, `request_id`, `http_status`, and the full parsed envelope as `body`.

```python
import snipget

client = snipget.Client()

try:
    resp = client.call("/healthcare/npi/validate", {"npi": "1234567893"})
except snipget.AuthenticationError as e:
    print("Check your API key:", e.error_code)            # 401/403
except snipget.InvalidRequestError as e:
    print("Bad request:", e.body.get("details"))           # 400/422
except snipget.RateLimitError as e:
    print("Throttled; retry in", e.retry_after, "seconds")  # 429 RATE_LIMITED
except snipget.QuotaExceededError as e:
    print("Out of monthly capacity:", e.body.get("limit_type"))
    print("Allowance left (USD):", e.credit_remaining_usd)  # 429 QUOTA_EXCEEDED
except snipget.MaintenanceError as e:
    print("Maintenance window; retry in", e.retry_after)    # 503 MAINTENANCE_MODE
except snipget.APIError as e:
    print("Server error; quote this id to support:", e.request_id)
```

The two 429s mean different things: `RateLimitError` is a per-second throughput throttle and clears in seconds; `QuotaExceededError` means the monthly included calls or prepaid overage allowance are exhausted and will not clear until the monthly reset, a tier upgrade, or an allowance top-up. The client retries the first automatically and never retries the second.

## Retries and timeouts

```python
client = Client(
    api_key="...",
    timeout=30.0,      # per-request timeout in seconds
    max_retries=2,     # retries on top of the initial attempt
)
```

The client automatically retries network errors, `RATE_LIMITED` 429s (honoring the server's `Retry-After`), and 5xx responses, using exponential backoff with jitter. Snipget utility calls are pure and idempotent, so retrying a POST is safe. It never retries `QUOTA_EXCEEDED` or any other 4xx. Maintenance 503s are retried on the short backoff only; if the window outlasts the retry budget you get a `MaintenanceError` with `retry_after` (typically 300 seconds) so you can schedule your own retry.

## The response envelope

Every Snipget endpoint, success or error, returns one envelope shape. `call()` returns a `SnipgetResponse`:

| Attribute | Type | Meaning |
| --- | --- | --- |
| `result` | endpoint-specific | The payload, exactly as the API returned it |
| `confidence` | `float` | 0.0-1.0 confidence score (1.0 = deterministic match; batch responses always report 1.0 at the top level, with per-item confidences inside `result.items`) |
| `status` | `str` | `"ok"` on success |
| `meta.cost_units` | `int` | Billable units consumed by this call |
| `meta.request_id` | `str` | Server request id; quote it to support |
| `meta.elapsed_ms` | `int` | Server-side processing time |
| `meta.version` | `str` | API version |
| `meta.rate_limit_remaining` / `meta.rate_limit_reset` | `int` | Throughput headroom and bucket reset (unix time) |
| `meta.quota_remaining` / `meta.quota_reset` | `int` | Monthly included-call headroom and reset (unix time) |
| `meta.credit_remaining_usd` | `float` | Live prepaid-allowance balance, populated once a call starts burning allowance |
| `meta.trace` | `list[str]` | Reasoning trace, when the request set `include_trace: true` |
| `raw` | `dict` | The full unmodified envelope |

Meta fields the server didn't send are `None`; unknown future fields stay available via `meta.raw`.

## Links

- Website: [https://snipget.ai](https://snipget.ai)
- Interactive API docs: [https://api.snipget.ai/docs](https://api.snipget.ai/docs)
- OpenAPI spec: [https://api.snipget.ai/openapi.json](https://api.snipget.ai/openapi.json)
- npm sibling: a JavaScript/TypeScript client (`snipget` on npm) is planned but not yet published

## License

MIT. Copyright 2026 Snipget Inc.
