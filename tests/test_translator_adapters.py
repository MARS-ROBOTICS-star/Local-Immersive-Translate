from __future__ import annotations

import unittest
import builtins
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from local_babeldoc_server.translation_types import AdapterRequest
from local_babeldoc_server.translation_types import ProviderCapabilities
from local_babeldoc_server.translator_adapters import GeminiInteractionsAdapter
from local_babeldoc_server.translator_adapters import DeepSeekChatAdapter
from local_babeldoc_server.translator_adapters import ModelConfigurationError
from local_babeldoc_server.translator_adapters import OpenAICompatibleAdapter
from local_babeldoc_server.translator_adapters import (
    UncontrolledBillableReasoningError,
)
from local_babeldoc_server.translator_adapters import resolve_adapter
from local_babeldoc_server.translator_adapters import _classify_transport_error


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class RecordingInteractions:
    def __init__(self, response):
        self.response = response
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


class ExplodingTokenCounter:
    def __init__(self):
        self.calls = 0

    def count_tokens(self, **_kwargs):
        self.calls += 1
        raise AssertionError("remote countTokens request was sent")


class RecordingChatCompletions:
    def __init__(self, response):
        self.response = response
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


class HttpError(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


def openai_response(*, usage=None):
    return SimpleNamespace(
        id="chatcmpl-1",
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="译文"),
                finish_reason="stop",
            )
        ],
        usage=usage,
    )


NON_REASONING_CAPABILITIES = ProviderCapabilities(
    supports_structured_output=True,
    supports_reasoning_control=False,
    supported_reasoning_levels=frozenset(),
    exposes_reasoning_usage=False,
    supports_cached_usage=False,
    supports_count_tokens=False,
    supports_request_id=True,
)

MINIMAL_CAPABILITIES = ProviderCapabilities(
    supports_structured_output=True,
    supports_reasoning_control=True,
    supported_reasoning_levels=frozenset({"minimal", "low", "medium", "high"}),
    exposes_reasoning_usage=True,
    supports_cached_usage=True,
    supports_count_tokens=False,
    supports_request_id=True,
)


class TranslatorAdaptersTest(unittest.TestCase):
    def test_classifies_provider_failures_into_actionable_codes(self) -> None:
        cases = (
            (
                HttpError(402, "Your prepayment credits are depleted"),
                "model_credit_exhausted",
                False,
            ),
            (
                HttpError(400, "This API is not available in your current location"),
                "model_region_unavailable",
                False,
            ),
            (HttpError(401, "invalid key"), "model_auth_failed", False),
            (HttpError(403, "permission denied"), "model_permission_denied", False),
            (HttpError(404, "model missing"), "model_not_found", False),
            (
                HttpError(408, "request timeout"),
                "transport_timeout_unknown_billing",
                False,
            ),
            (
                HttpError(429, "RESOURCE_EXHAUSTED: quota exceeded"),
                "model_quota_exhausted",
                True,
            ),
            (HttpError(502, "bad gateway"), "transport_unavailable", True),
        )

        for error, code, retryable in cases:
            with self.subTest(code=code):
                failure = _classify_transport_error(error)
                self.assertEqual(failure.error_code, code)
                self.assertIs(failure.retryable, retryable)

    def test_gemini_count_tokens_is_local_and_includes_schema(self) -> None:
        models = ExplodingTokenCounter()
        adapter = GeminiInteractionsAdapter(
            api_key="secret",
            model="gemini-3.1-flash-lite",
            client=SimpleNamespace(models=models),
        )
        request = AdapterRequest(
            model=adapter.model,
            input="abc",
            response_format={
                "type": "text",
                "schema": {"type": "object"},
            },
        )

        bound = adapter.count_tokens(request)

        self.assertGreater(bound, len("abc"))
        self.assertEqual(models.calls, 0)

    def test_missing_google_sdk_reports_backend_reinstall_action(self) -> None:
        original_import = builtins.__import__

        def import_without_google(name, *args, **kwargs):
            if name == "google":
                raise ImportError("cannot import name 'genai' from 'google'")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=import_without_google):
            with self.assertRaisesRegex(
                ModelConfigurationError,
                "google-genai.*reinstall the local backend dependencies",
            ):
                GeminiInteractionsAdapter(
                    api_key="secret",
                    model="gemini-3.6-flash",
                )

    def test_deepseek_uses_json_object_disables_thinking_and_maps_cache(self) -> None:
        usage = SimpleNamespace(
            prompt_tokens=120,
            completion_tokens=30,
            total_tokens=150,
            prompt_cache_hit_tokens=45,
            prompt_cache_miss_tokens=75,
        )
        completions = RecordingChatCompletions(openai_response(usage=usage))
        client = SimpleNamespace(
            chat=SimpleNamespace(completions=completions)
        )
        adapter = DeepSeekChatAdapter(
            api_key="secret",
            base_url="https://api.deepseek.com",
            model="deepseek-chat",
            client=client,
        )

        response = adapter.send(
            AdapterRequest(
                model="deepseek-chat",
                input="translate",
                max_output_tokens=2048,
                response_format={
                    "type": "text",
                    "mime_type": "application/json",
                    "schema": {"type": "object"},
                },
                reasoning_level="minimal",
            )
        )

        sent = completions.calls[0]
        self.assertEqual(sent["response_format"], {"type": "json_object"})
        self.assertEqual(
            sent["extra_body"],
            {"thinking": {"type": "disabled"}},
        )
        self.assertNotIn("reasoning_effort", sent)
        self.assertEqual(response.usage.prompt_tokens, 120)
        self.assertEqual(response.usage.cached_tokens, 45)
        self.assertEqual(response.usage.visible_completion_tokens, 30)
        self.assertEqual(response.usage.reasoning_tokens, 0)
        self.assertEqual(response.usage.total_tokens, 150)

    def test_resolver_selects_deepseek_adapter_by_provider_capability(self) -> None:
        client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=RecordingChatCompletions(openai_response())
            )
        )

        adapter = resolve_adapter(
            {
                "provider": "deepseek",
                "api_surface": "openai-chat-completions",
                "base_url": "https://api.deepseek.com",
                "api_key": "secret",
                "model": "deepseek-chat",
            },
            client=client,
        )

        self.assertIsInstance(adapter, DeepSeekChatAdapter)

    def test_gemini_uses_v1_stateless_minimal_structured_request(self) -> None:
        usage = SimpleNamespace(
            total_input_tokens=120,
            total_output_tokens=30,
            total_thought_tokens=7,
            total_cached_tokens=40,
            total_tool_use_tokens=0,
            total_tokens=157,
        )
        interaction = SimpleNamespace(
            id="interaction-1",
            output_text='{"translations": []}',
            status="completed",
            steps=[SimpleNamespace(type="text")],
            usage=usage,
        )
        interactions = RecordingInteractions(interaction)
        client = SimpleNamespace(interactions=interactions)
        factory_calls: list[dict] = []

        def client_factory(**kwargs):
            factory_calls.append(kwargs)
            return client

        adapter = GeminiInteractionsAdapter(
            api_key="secret",
            model="gemini-3.6-flash",
            client_factory=client_factory,
        )
        schema = {"type": "object", "properties": {}}
        response = adapter.send(
            AdapterRequest(
                model="gemini-3.6-flash",
                input="translate",
                max_output_tokens=4096,
                response_format={
                    "type": "text",
                    "mime_type": "application/json",
                    "schema": schema,
                },
                reasoning_level="minimal",
            )
        )

        self.assertEqual(factory_calls[0]["http_options"], {"api_version": "v1"})
        sent = interactions.calls[0]
        self.assertFalse(sent["store"])
        self.assertNotIn("previous_interaction_id", sent)
        self.assertEqual(sent["response_format"]["schema"], schema)
        self.assertEqual(sent["generation_config"]["thinking_level"], "minimal")
        self.assertEqual(sent["generation_config"]["max_output_tokens"], 4096)
        self.assertNotIn("temperature", sent["generation_config"])
        self.assertEqual(response.provider_request_id, "interaction-1")
        self.assertEqual(response.usage.prompt_tokens, 120)
        self.assertEqual(response.usage.visible_completion_tokens, 30)
        self.assertEqual(response.usage.reasoning_tokens, 7)
        self.assertEqual(response.usage.cached_tokens, 40)
        self.assertEqual(response.usage.tool_use_tokens, 0)
        self.assertEqual(response.usage.total_tokens, 157)

    def test_gemini_omits_response_format_for_plain_text_fallback(self) -> None:
        interaction = SimpleNamespace(
            id="interaction-plain-1",
            output_text="译文",
            status="completed",
            steps=[],
            usage=SimpleNamespace(
                total_input_tokens=10,
                total_output_tokens=2,
                total_thought_tokens=0,
                total_cached_tokens=0,
                total_tool_use_tokens=0,
                total_tokens=12,
            ),
        )
        interactions = RecordingInteractions(interaction)
        adapter = GeminiInteractionsAdapter(
            api_key="secret",
            model="gemini-3.6-flash",
            client=SimpleNamespace(interactions=interactions),
        )

        response = adapter.send(
            AdapterRequest(
                model="gemini-3.6-flash",
                input="Translate this plain text.",
                max_output_tokens=2048,
                response_format=None,
                reasoning_level="minimal",
            )
        )

        self.assertEqual(response.output_text, "译文")
        self.assertNotIn("response_format", interactions.calls[0])

    def test_openai_client_disables_retries_and_omits_reasoning_for_plain_model(self) -> None:
        completions = RecordingChatCompletions(openai_response())
        client = SimpleNamespace(
            chat=SimpleNamespace(completions=completions)
        )
        factory_calls: list[dict] = []

        def client_factory(**kwargs):
            factory_calls.append(kwargs)
            return client

        adapter = OpenAICompatibleAdapter(
            api_key="secret",
            base_url="https://compatible.invalid/v1",
            model="plain-model",
            capabilities=NON_REASONING_CAPABILITIES,
            default_billable_reasoning=False,
            client_factory=client_factory,
        )
        response = adapter.send(
            AdapterRequest(
                model="plain-model",
                input=[{"role": "user", "content": "text"}],
                max_output_tokens=2048,
            )
        )

        self.assertEqual(factory_calls[0]["max_retries"], 0)
        sent = completions.calls[0]
        self.assertNotIn("reasoning_effort", sent)
        self.assertNotIn("temperature", sent)
        self.assertEqual(sent["max_tokens"], 2048)
        self.assertEqual(response.output_text, "译文")
        self.assertIsNone(response.usage.reasoning_tokens)

    def test_openai_sends_minimal_only_when_capability_declares_it(self) -> None:
        completions = RecordingChatCompletions(openai_response())
        adapter = OpenAICompatibleAdapter(
            api_key="secret",
            base_url="https://compatible.invalid/v1",
            model="reasoning-model",
            capabilities=MINIMAL_CAPABILITIES,
            default_billable_reasoning=True,
            client=SimpleNamespace(
                chat=SimpleNamespace(completions=completions)
            ),
        )

        adapter.send(
            AdapterRequest(
                model="reasoning-model",
                input=[{"role": "user", "content": "text"}],
                reasoning_level="minimal",
            )
        )

        self.assertEqual(
            completions.calls[0]["reasoning_effort"],
            "minimal",
        )

    def test_openai_converts_canonical_schema_to_strict_json_schema(self) -> None:
        completions = RecordingChatCompletions(openai_response())
        adapter = OpenAICompatibleAdapter(
            api_key="secret",
            base_url="https://compatible.invalid/v1",
            model="plain-model",
            capabilities=NON_REASONING_CAPABILITIES,
            default_billable_reasoning=False,
            client=SimpleNamespace(
                chat=SimpleNamespace(completions=completions)
            ),
        )
        schema = {"type": "object", "properties": {}}

        adapter.send(
            AdapterRequest(
                model="plain-model",
                input="translate",
                response_format={
                    "type": "text",
                    "mime_type": "application/json",
                    "schema": schema,
                },
            )
        )

        self.assertEqual(
            completions.calls[0]["response_format"],
            {
                "type": "json_schema",
                "json_schema": {
                    "name": "translations",
                    "strict": True,
                    "schema": schema,
                },
            },
        )

    def test_uncontrolled_default_billable_reasoning_fails_before_send(self) -> None:
        with self.assertRaises(UncontrolledBillableReasoningError):
            OpenAICompatibleAdapter(
                api_key="secret",
                base_url="https://compatible.invalid/v1",
                model="uncontrolled-model",
                capabilities=NON_REASONING_CAPABILITIES,
                default_billable_reasoning=True,
                client=SimpleNamespace(),
            )

    def test_strict_schema_request_is_rejected_when_capability_is_false(self) -> None:
        capabilities = ProviderCapabilities(
            supports_structured_output=False,
            supports_reasoning_control=False,
            supported_reasoning_levels=frozenset(),
            exposes_reasoning_usage=False,
            supports_cached_usage=False,
            supports_count_tokens=False,
            supports_request_id=False,
        )
        adapter = OpenAICompatibleAdapter(
            api_key="secret",
            base_url="https://compatible.invalid/v1",
            model="no-schema",
            capabilities=capabilities,
            default_billable_reasoning=False,
            client=SimpleNamespace(
                chat=SimpleNamespace(
                    completions=RecordingChatCompletions(openai_response())
                )
            ),
        )

        with self.assertRaises(ModelConfigurationError):
            adapter.send(
                AdapterRequest(
                    model="no-schema",
                    input=[{"role": "user", "content": "text"}],
                    response_format={"type": "json_schema"},
                )
            )

    def test_resolver_uses_api_surface_and_explicit_capabilities(self) -> None:
        google = resolve_adapter(
            {
                "provider": "google",
                "api_surface": "interactions-v1",
                "api_key": "secret",
                "model": "gemini-3.6-flash",
            },
            client=SimpleNamespace(interactions=RecordingInteractions(None)),
        )
        compatible = resolve_adapter(
            {
                "provider": "third-party",
                "api_surface": "openai-chat-completions",
                "base_url": "https://compatible.invalid/v1",
                "api_key": "secret",
                "model": "plain-model",
                "default_billable_reasoning": False,
                "capabilities": {
                    "supports_structured_output": True,
                    "supports_reasoning_control": False,
                    "supported_reasoning_levels": [],
                    "exposes_reasoning_usage": False,
                    "supports_cached_usage": False,
                    "supports_count_tokens": False,
                    "supports_request_id": True,
                },
            },
            client=SimpleNamespace(
                chat=SimpleNamespace(
                    completions=RecordingChatCompletions(openai_response())
                )
            ),
        )

        self.assertIsInstance(google, GeminiInteractionsAdapter)
        self.assertIsInstance(compatible, OpenAICompatibleAdapter)

    def test_google_sdk_version_is_pinned(self) -> None:
        requirements = (
            PROJECT_ROOT / "local_babeldoc_server" / "requirements.txt"
        ).read_text(encoding="utf-8")

        self.assertIn("google-genai==2.6.0", requirements.splitlines())


if __name__ == "__main__":
    unittest.main()
