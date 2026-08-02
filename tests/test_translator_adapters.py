from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace

from local_babeldoc_server.translation_types import AdapterRequest
from local_babeldoc_server.translation_types import ProviderCapabilities
from local_babeldoc_server.translator_adapters import GeminiInteractionsAdapter
from local_babeldoc_server.translator_adapters import ModelConfigurationError
from local_babeldoc_server.translator_adapters import OpenAICompatibleAdapter
from local_babeldoc_server.translator_adapters import (
    UncontrolledBillableReasoningError,
)
from local_babeldoc_server.translator_adapters import resolve_adapter


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class RecordingInteractions:
    def __init__(self, response):
        self.response = response
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


class RecordingChatCompletions:
    def __init__(self, response):
        self.response = response
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


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
