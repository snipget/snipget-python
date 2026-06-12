"""Typed views over the Snipget success envelope.

Every Snipget endpoint returns the same JSON envelope:

    {
      "status": "ok",
      "confidence": 0.92,
      "result": {...},
      "meta": {"version": "...", "elapsed_ms": 3, "cost_units": 1,
               "request_id": "req_...", ...}
    }

These classes only *carry* that envelope; they never reshape or interpret
``result``. The per-endpoint result schemas live in the OpenAPI spec at
https://api.snipget.ai/openapi.json.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = ["ResponseMeta", "SnipgetResponse"]


def _opt_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _opt_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    return float(value) if isinstance(value, (int, float)) else None


@dataclass(frozen=True)
class ResponseMeta:
    """Typed view of the envelope's ``meta`` object.

    Fields the server didn't send are ``None``. The untouched meta dict is
    available as ``raw`` (so additive server-side fields are never lost).
    """

    version: str | None = None
    elapsed_ms: int | None = None
    cost_units: int | None = None
    request_id: str | None = None
    trace: list[str] | None = None
    rate_limit_remaining: int | None = None
    rate_limit_reset: int | None = None  # unix timestamp
    quota_remaining: int | None = None
    quota_reset: int | None = None  # unix timestamp (start of next UTC month)
    credit_remaining_usd: float | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResponseMeta:
        return cls(
            version=data.get("version"),
            elapsed_ms=_opt_int(data.get("elapsed_ms")),
            cost_units=_opt_int(data.get("cost_units")),
            request_id=data.get("request_id"),
            trace=data.get("trace"),
            rate_limit_remaining=_opt_int(data.get("rate_limit_remaining")),
            rate_limit_reset=_opt_int(data.get("rate_limit_reset")),
            quota_remaining=_opt_int(data.get("quota_remaining")),
            quota_reset=_opt_int(data.get("quota_reset")),
            credit_remaining_usd=_opt_float(data.get("credit_remaining_usd")),
            raw=data,
        )


@dataclass(frozen=True)
class SnipgetResponse:
    """One parsed success envelope.

    Attributes:
        status: Always ``"ok"`` for a success envelope.
        confidence: 0.0-1.0 confidence score for the result.
        result: The endpoint-specific payload, exactly as the API sent it.
        meta: Typed metadata (cost_units, request_id, rate-limit and
            quota headroom, ...).
        raw: The full unmodified envelope dict.
    """

    status: str
    confidence: float
    result: Any
    meta: ResponseMeta
    raw: dict[str, Any] = field(repr=False, default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SnipgetResponse:
        meta = data.get("meta")
        return cls(
            status=data.get("status", "ok"),
            confidence=float(data.get("confidence", 0.0)),
            result=data.get("result"),
            meta=ResponseMeta.from_dict(meta if isinstance(meta, dict) else {}),
            raw=data,
        )
