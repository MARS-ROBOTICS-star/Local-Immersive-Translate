from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from typing import Callable
from typing import Mapping
from typing import Protocol

from local_babeldoc_server.translation_types import AdapterRequest
from local_babeldoc_server.translation_types import AdapterResponse
from local_babeldoc_server.translation_types import BillingStatus
from local_babeldoc_server.translation_types import NormalizedUsage
from local_babeldoc_server.translation_types import ProviderCapabilities
from local_babeldoc_server.translation_types import TransportOutcome


class ModelConfigurationError(RuntimeError):
    pass


class UncontrolledBillableReasoningError(ModelConfigurationError):
    pass


@dataclass(frozen=True, slots=True)
class TransportFailure:
    outcome: TransportOutcome
    billing_status: BillingStatus
    retryable: bool
    error_code: str


class ProviderAdapter(Protocol):
    capabilities: ProviderCapabilities
    model: str

    def count_tokens(self, request: AdapterRequest) -> int: ...

    def validate_request(self, request: AdapterRequest) -> None: ...

    def send(self, request: AdapterRequest) -> AdapterResponse: ...

    def classify_error(self, error: Exception) -> TransportFailure: ...


def _value(value: Any, name: str) -> Any:
    if value is None:
        return None
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _nullable_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def _conservative_local_token_bound(request: AdapterRequest) -> int:
    serialized = json.dumps(request.input, ensure_ascii=False, default=str)
    if request.response_format is not None:
        serialized += json.dumps(
            request.response_format,
            ensure_ascii=False,
            default=str,
        )
    return len(serialized.encode("utf-8")) + 64


def _classify_transport_error(error: Exception) -> TransportFailure:
    status_code = getattr(error, "status_code", None)
    if status_code is None:
        response = getattr(error, "response", None)
        status_code = getattr(response, "status_code", None)
    if status_code == 429:
        return TransportFailure(
            outcome=TransportOutcome.RATE_LIMITED,
            billing_status=BillingStatus.NOT_BILLABLE,
            retryable=True,
            error_code="transport_rate_limited",
        )
    if status_code == 503:
        return TransportFailure(
            outcome=TransportOutcome.UNAVAILABLE,
            billing_status=BillingStatus.NOT_BILLABLE,
            retryable=True,
            error_code="transport_unavailable",
        )
    name = type(error).__name__.casefold()
    if "connecttimeout" in name or "connect_timeout" in name:
        return TransportFailure(
            outcome=TransportOutcome.CONNECT_TIMEOUT,
            billing_status=BillingStatus.NOT_BILLABLE,
            retryable=False,
            error_code="transport_connect_timeout",
        )
    if "readtimeout" in name or "timeout" in name:
        return TransportFailure(
            outcome=TransportOutcome.RESPONSE_TIMEOUT,
            billing_status=BillingStatus.UNKNOWN,
            retryable=False,
            error_code="transport_timeout_unknown_billing",
        )
    if status_code is not None and 400 <= int(status_code) < 500:
        return TransportFailure(
            outcome=TransportOutcome.CLIENT_ERROR,
            billing_status=BillingStatus.NOT_BILLABLE,
            retryable=False,
            error_code="model_configuration_error",
        )
    return TransportFailure(
        outcome=TransportOutcome.UNAVAILABLE,
        billing_status=BillingStatus.UNKNOWN,
        retryable=False,
        error_code="transport_unknown",
    )


GEMINI_INTERACTIONS_CAPABILITIES = ProviderCapabilities(
    supports_structured_output=True,
    supports_reasoning_control=True,
    supported_reasoning_levels=frozenset(
        {"minimal", "low", "medium", "high"}
    ),
    exposes_reasoning_usage=True,
    supports_cached_usage=True,
    supports_count_tokens=True,
    supports_request_id=True,
)


class GeminiInteractionsAdapter:
    capabilities = GEMINI_INTERACTIONS_CAPABILITIES

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        client: Any = None,
        client_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.model = model
        if client is not None:
            self.client = client
            return
        if client_factory is None:
            try:
                from google import genai
            except ImportError as exc:
                raise ModelConfigurationError(
                    "Gemini requires google-genai; reinstall the local "
                    "backend dependencies"
                ) from exc

            client_factory = genai.Client
        self.client = client_factory(
            api_key=api_key,
            http_options={"api_version": "v1"},
        )

    def count_tokens(self, request: AdapterRequest) -> int:
        models = getattr(self.client, "models", None)
        counter = getattr(models, "count_tokens", None)
        if not callable(counter):
            return _conservative_local_token_bound(request)
        result = counter(model=self.model, contents=request.input)
        counted = _value(result, "total_tokens")
        if counted is None:
            return _conservative_local_token_bound(request)
        return int(counted)

    def validate_request(self, request: AdapterRequest) -> None:
        level = request.reasoning_level or "minimal"
        if level not in self.capabilities.supported_reasoning_levels:
            raise ModelConfigurationError(
                f"unsupported Gemini reasoning level: {level}"
            )
        if (
            request.response_format is not None
            and not self.capabilities.supports_structured_output
        ):
            raise ModelConfigurationError(
                f"model {self.model} does not support strict structured output"
            )

    def send(self, request: AdapterRequest) -> AdapterResponse:
        self.validate_request(request)
        level = request.reasoning_level or "minimal"
        options: dict[str, Any] = {
            "model": self.model,
            "input": request.input,
            "generation_config": {
                "thinking_level": level,
                "max_output_tokens": request.max_output_tokens,
            },
            "store": False,
        }
        if request.response_format is not None:
            options["response_format"] = request.response_format
        response = self.client.interactions.create(**options)
        usage = _value(response, "usage")
        normalized_usage = NormalizedUsage(
            prompt_tokens=_nullable_int(
                _value(usage, "total_input_tokens")
            ),
            visible_completion_tokens=_nullable_int(
                _value(usage, "total_output_tokens")
            ),
            reasoning_tokens=_nullable_int(
                _value(usage, "total_thought_tokens")
            ),
            cached_tokens=_nullable_int(
                _value(usage, "total_cached_tokens")
            ),
            tool_use_tokens=_nullable_int(
                _value(usage, "total_tool_use_tokens")
            ),
            total_tokens=_nullable_int(_value(usage, "total_tokens")),
        )
        output_text = _value(response, "output_text")
        if output_text is None:
            output_text = self._text_from_steps(_value(response, "steps"))
        return AdapterResponse(
            output_text=str(output_text or ""),
            usage=normalized_usage,
            provider_request_id=_value(response, "id"),
            finish_reason=str(_value(response, "status") or "") or None,
        )

    @staticmethod
    def _text_from_steps(steps: Any) -> str:
        pieces: list[str] = []
        for step in steps or []:
            content = _value(step, "content")
            if not isinstance(content, (list, tuple)):
                continue
            for item in content:
                text = _value(item, "text")
                if text:
                    pieces.append(str(text))
        return "".join(pieces)

    def classify_error(self, error: Exception) -> TransportFailure:
        return _classify_transport_error(error)


class OpenAICompatibleAdapter:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        capabilities: ProviderCapabilities,
        default_billable_reasoning: bool,
        client: Any = None,
        client_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.model = model
        self.capabilities = capabilities
        self.default_billable_reasoning = bool(default_billable_reasoning)
        if self.default_billable_reasoning and (
            not capabilities.supports_reasoning_control
            or "minimal" not in capabilities.supported_reasoning_levels
        ):
            raise UncontrolledBillableReasoningError(
                f"model {model} has uncontrolled billable reasoning"
            )
        if client is not None:
            self.client = client
            return
        if client_factory is None:
            import openai

            client_factory = openai.OpenAI
        self.client = client_factory(
            base_url=base_url,
            api_key=api_key,
            max_retries=0,
            timeout=600,
        )

    def count_tokens(self, request: AdapterRequest) -> int:
        return _conservative_local_token_bound(request)

    def validate_request(self, request: AdapterRequest) -> None:
        if (
            request.response_format is not None
            and not self.capabilities.supports_structured_output
        ):
            raise ModelConfigurationError(
                f"model {self.model} does not support strict structured output"
            )
        if self.capabilities.supports_reasoning_control:
            level = request.reasoning_level or "minimal"
            if level not in self.capabilities.supported_reasoning_levels:
                raise ModelConfigurationError(
                    f"model {self.model} does not support reasoning level {level}"
                )

    def send(self, request: AdapterRequest) -> AdapterResponse:
        self.validate_request(request)
        messages = request.input
        if isinstance(messages, str):
            messages = [{"role": "user", "content": messages}]
        options: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": request.max_output_tokens,
        }
        if request.response_format is not None:
            response_format = request.response_format
            if (
                response_format.get("type") == "text"
                and response_format.get("mime_type") == "application/json"
                and isinstance(response_format.get("schema"), dict)
            ):
                response_format = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "translations",
                        "strict": True,
                        "schema": response_format["schema"],
                    },
                }
            options["response_format"] = response_format
        if request.extra_headers:
            options["extra_headers"] = dict(request.extra_headers)
        if self.capabilities.supports_reasoning_control:
            level = request.reasoning_level or "minimal"
            options["reasoning_effort"] = level
        response = self.client.chat.completions.create(**options)
        choices = _value(response, "choices") or []
        choice = choices[0] if choices else None
        message = _value(choice, "message")
        return AdapterResponse(
            output_text=str(_value(message, "content") or ""),
            usage=NormalizedUsage.from_openai_usage(
                _value(response, "usage")
            ),
            provider_request_id=_value(response, "id"),
            finish_reason=_value(choice, "finish_reason"),
        )

    def classify_error(self, error: Exception) -> TransportFailure:
        return _classify_transport_error(error)


DEEPSEEK_CHAT_CAPABILITIES = ProviderCapabilities(
    supports_structured_output=True,
    supports_reasoning_control=True,
    supported_reasoning_levels=frozenset({"minimal"}),
    exposes_reasoning_usage=False,
    supports_cached_usage=True,
    supports_count_tokens=False,
    supports_request_id=True,
)


class DeepSeekChatAdapter(OpenAICompatibleAdapter):
    """DeepSeek chat-completions contract with paid thinking disabled."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        client: Any = None,
        client_factory: Callable[..., Any] | None = None,
    ) -> None:
        super().__init__(
            api_key=api_key,
            base_url=base_url,
            model=model,
            capabilities=DEEPSEEK_CHAT_CAPABILITIES,
            default_billable_reasoning=False,
            client=client,
            client_factory=client_factory,
        )

    def send(self, request: AdapterRequest) -> AdapterResponse:
        self.validate_request(request)
        messages = request.input
        if isinstance(messages, str):
            messages = [{"role": "user", "content": messages}]
        options: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": request.max_output_tokens,
            "extra_body": {"thinking": {"type": "disabled"}},
        }
        if request.response_format is not None:
            options["response_format"] = {"type": "json_object"}
        if request.extra_headers:
            options["extra_headers"] = dict(request.extra_headers)
        response = self.client.chat.completions.create(**options)
        choices = _value(response, "choices") or []
        choice = choices[0] if choices else None
        message = _value(choice, "message")
        usage = _value(response, "usage")
        cached_tokens = _nullable_int(
            _value(usage, "prompt_cache_hit_tokens")
        )
        if cached_tokens is None:
            prompt_details = _value(usage, "prompt_tokens_details")
            cached_tokens = _nullable_int(
                _value(prompt_details, "cached_tokens")
            )
        return AdapterResponse(
            output_text=str(_value(message, "content") or ""),
            usage=NormalizedUsage(
                prompt_tokens=_nullable_int(_value(usage, "prompt_tokens")),
                visible_completion_tokens=_nullable_int(
                    _value(usage, "completion_tokens")
                ),
                reasoning_tokens=0,
                cached_tokens=cached_tokens,
                tool_use_tokens=None,
                total_tokens=_nullable_int(_value(usage, "total_tokens")),
            ),
            provider_request_id=_value(response, "id"),
            finish_reason=_value(choice, "finish_reason"),
        )


def _capabilities_from_config(
    config: Mapping[str, Any],
) -> ProviderCapabilities:
    return ProviderCapabilities(
        supports_structured_output=bool(
            config.get("supports_structured_output", False)
        ),
        supports_reasoning_control=bool(
            config.get("supports_reasoning_control", False)
        ),
        supported_reasoning_levels=frozenset(
            str(value)
            for value in config.get("supported_reasoning_levels", [])
        ),
        exposes_reasoning_usage=bool(
            config.get("exposes_reasoning_usage", False)
        ),
        supports_cached_usage=bool(
            config.get("supports_cached_usage", False)
        ),
        supports_count_tokens=bool(
            config.get("supports_count_tokens", False)
        ),
        supports_request_id=bool(config.get("supports_request_id", False)),
    )


def resolve_adapter(
    model_config: Mapping[str, Any],
    *,
    client: Any = None,
    client_factory: Callable[..., Any] | None = None,
) -> ProviderAdapter:
    provider = str(model_config.get("provider") or "").casefold()
    api_surface = str(model_config.get("api_surface") or "").casefold()
    if api_surface == "interactions-v1" or (
        provider in {"google", "gemini"} and not api_surface
    ):
        return GeminiInteractionsAdapter(
            api_key=str(model_config.get("api_key") or ""),
            model=str(model_config.get("model") or ""),
            client=client,
            client_factory=client_factory,
        )
    if api_surface not in {"", "openai-chat-completions"}:
        raise ModelConfigurationError(
            f"unsupported API surface: {api_surface}"
        )
    if provider == "deepseek":
        return DeepSeekChatAdapter(
            api_key=str(model_config.get("api_key") or ""),
            base_url=str(model_config.get("base_url") or ""),
            model=str(model_config.get("model") or ""),
            client=client,
            client_factory=client_factory,
        )
    capabilities = _capabilities_from_config(
        model_config.get("capabilities") or {}
    )
    return OpenAICompatibleAdapter(
        api_key=str(model_config.get("api_key") or ""),
        base_url=str(model_config.get("base_url") or ""),
        model=str(model_config.get("model") or ""),
        capabilities=capabilities,
        default_billable_reasoning=bool(
            model_config.get("default_billable_reasoning", False)
        ),
        client=client,
        client_factory=client_factory,
    )
