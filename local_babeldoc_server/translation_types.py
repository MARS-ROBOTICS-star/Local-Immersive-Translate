from __future__ import annotations

from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field
from decimal import Decimal
from enum import Enum
from typing import Any
from typing import Literal
from typing import Mapping


class BillingStatus(str, Enum):
    CONFIRMED = "confirmed"
    NOT_BILLABLE = "not_billable"
    UNKNOWN = "unknown"


class TransportOutcome(str, Enum):
    COMPLETED = "completed"
    RATE_LIMITED = "rate_limited"
    UNAVAILABLE = "unavailable"
    CONNECT_TIMEOUT = "connect_timeout"
    RESPONSE_TIMEOUT = "response_timeout"
    CLIENT_ERROR = "client_error"
    CONFIGURATION_ERROR = "configuration_error"


@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    supports_structured_output: bool
    supports_reasoning_control: bool
    supported_reasoning_levels: frozenset[str]
    exposes_reasoning_usage: bool
    supports_cached_usage: bool
    supports_count_tokens: bool
    supports_request_id: bool


def _field_value(value: Any, name: str) -> Any:
    if value is None:
        return None
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _nullable_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


@dataclass(frozen=True, slots=True)
class NormalizedUsage:
    prompt_tokens: int | None = None
    visible_completion_tokens: int | None = None
    reasoning_tokens: int | None = None
    cached_tokens: int | None = None
    tool_use_tokens: int | None = None
    total_tokens: int | None = None

    @classmethod
    def from_openai_usage(cls, usage: Any) -> "NormalizedUsage":
        if usage is None:
            return cls()
        prompt_tokens = _nullable_int(_field_value(usage, "prompt_tokens"))
        completion_tokens = _nullable_int(
            _field_value(usage, "completion_tokens")
        )
        completion_details = _field_value(
            usage,
            "completion_tokens_details",
        )
        reasoning_tokens = _nullable_int(
            _field_value(completion_details, "reasoning_tokens")
        )
        prompt_details = _field_value(usage, "prompt_tokens_details")
        cached_tokens = _nullable_int(
            _field_value(prompt_details, "cached_tokens")
        )
        visible_completion_tokens = completion_tokens
        if completion_tokens is not None and reasoning_tokens is not None:
            visible_completion_tokens = max(
                0,
                completion_tokens - reasoning_tokens,
            )
        return cls(
            prompt_tokens=prompt_tokens,
            visible_completion_tokens=visible_completion_tokens,
            reasoning_tokens=reasoning_tokens,
            cached_tokens=cached_tokens,
            tool_use_tokens=None,
            total_tokens=_nullable_int(_field_value(usage, "total_tokens")),
        )

    def to_dict(self) -> dict[str, int | None]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class PricingSnapshot:
    provider: str
    model: str
    api_surface: str
    service_tier: str
    input_usd_per_million: Decimal
    output_usd_per_million: Decimal
    cached_input_usd_per_million: Decimal
    usd_to_jpy: Decimal
    tax_included: bool
    captured_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "api_surface": self.api_surface,
            "service_tier": self.service_tier,
            "input_usd_per_million": str(self.input_usd_per_million),
            "output_usd_per_million": str(self.output_usd_per_million),
            "cached_input_usd_per_million": str(
                self.cached_input_usd_per_million
            ),
            "usd_to_jpy": str(self.usd_to_jpy),
            "tax_included": self.tax_included,
            "captured_at": self.captured_at,
        }


@dataclass(frozen=True, slots=True)
class RequestContext:
    document_id: str
    part_index: int | None
    request_category: Literal[
        "batch",
        "fallback",
        "quality_retry",
        "terminology",
        "model_test",
    ]
    paragraph_ids: tuple[str, ...]
    semantic_attempt_number: int | None
    transport_attempt_number: int
    source_token_estimate: int
    paragraph_count: int


@dataclass(frozen=True, slots=True)
class AdapterRequest:
    model: str
    input: Any
    max_output_tokens: int = 4096
    response_format: dict[str, Any] | None = None
    reasoning_level: str | None = None
    extra_headers: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AdapterResponse:
    output_text: str
    usage: NormalizedUsage
    provider_request_id: str | None = None
    finish_reason: str | None = None


def calculate_usage_cost(
    usage: NormalizedUsage,
    pricing: PricingSnapshot,
) -> Decimal:
    prompt_tokens = usage.prompt_tokens or 0
    cached_tokens = usage.cached_tokens or 0
    uncached_input_tokens = max(0, prompt_tokens - cached_tokens)
    output_tokens = usage.visible_completion_tokens or 0
    reasoning_tokens = usage.reasoning_tokens or 0
    return (
        Decimal(uncached_input_tokens) * pricing.input_usd_per_million
        + Decimal(cached_tokens) * pricing.cached_input_usd_per_million
        + Decimal(output_tokens + reasoning_tokens)
        * pricing.output_usd_per_million
    ) / Decimal(1_000_000)
