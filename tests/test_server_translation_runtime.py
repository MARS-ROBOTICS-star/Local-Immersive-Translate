from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from local_babeldoc_server.server import AppState
from local_babeldoc_server.server import DEFAULT_CONFIG
from local_babeldoc_server.server import Job
from local_babeldoc_server.server import deep_merge
from local_babeldoc_server.translation_runtime import RuntimeBackedTranslator
from local_babeldoc_server.translation_types import AdapterResponse
from local_babeldoc_server.translation_types import NormalizedUsage
from local_babeldoc_server.translation_types import ProviderCapabilities


class OfflineAdapter:
    model = "gemini-3.6-flash"
    capabilities = ProviderCapabilities(
        supports_structured_output=True,
        supports_reasoning_control=True,
        supported_reasoning_levels=frozenset({"minimal"}),
        exposes_reasoning_usage=True,
        supports_cached_usage=True,
        supports_count_tokens=True,
        supports_request_id=True,
    )

    def __init__(self):
        self.sent_requests = []

    def validate_request(self, request):
        return None

    def count_tokens(self, request):
        return 10

    def send(self, request):
        self.sent_requests.append(request)
        return AdapterResponse(
            json.dumps({"translation": "你好"}),
            NormalizedUsage(),
        )

    def classify_error(self, error):
        raise AssertionError("not used")


def make_job() -> Job:
    return Job(
        pdf_id="doc-1",
        object_key="paper.pdf",
        file_name="paper.pdf",
        request_model="gemini-1",
        target_language="zh",
        model_config=None,
        options={},
        created_at=0,
    )


class ServerTranslationRuntimeTest(unittest.TestCase):
    def test_installers_verify_pinned_google_sdk_without_creating_client(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        bash = (project_root / "scripts" / "install-local-backend.sh").read_text(
            encoding="utf-8"
        )
        powershell = (
            project_root / "scripts" / "install-local-backend.ps1"
        ).read_text(encoding="utf-8")

        self.assertIn("from google import genai", bash)
        self.assertIn("from google import genai", powershell)
        self.assertNotIn("genai.Client(", bash)
        self.assertNotIn("genai.Client(", powershell)

    def test_default_config_contains_hard_budget_batch_and_ocr_limits(self) -> None:
        babeldoc = DEFAULT_CONFIG["babeldoc"]

        self.assertEqual(babeldoc["max_requests_per_document"], 150)
        self.assertEqual(babeldoc["max_estimated_cost_jpy"], 300)
        self.assertEqual(babeldoc["max_semantic_attempts_per_paragraph"], 2)
        self.assertEqual(babeldoc["max_billable_exposures_per_paragraph"], 2)
        self.assertEqual(babeldoc["batch_target_source_tokens"], 1000)
        self.assertEqual(babeldoc["batch_max_source_tokens"], 1500)
        self.assertEqual(babeldoc["batch_max_paragraphs"], 16)
        self.assertEqual(babeldoc["max_table_ocr_paragraphs"], 200)
        self.assertEqual(babeldoc["max_ocr_blocks_per_table"], 100)
        self.assertEqual(babeldoc["max_table_ocr_to_native_ratio"], 0.5)
        self.assertEqual(babeldoc["ratio_check_min_native_paragraphs"], 20)

    def test_server_creates_runtime_and_runtime_backed_translator(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = deep_merge(
                DEFAULT_CONFIG,
                {
                    "babeldoc": {"data_dir": directory},
                    "models": {
                        "gemini-1": {
                            "provider": "google",
                            "api_surface": "interactions-v1",
                            "api_key": "secret",
                            "model": "gemini-3.6-flash",
                        }
                    },
                },
            )
            state = AppState(config)
            job = make_job()
            working = Path(directory) / "working" / job.pdf_id

            runtime, resolved_model = state._create_translation_runtime(
                job,
                working,
                adapter=OfflineAdapter(),
            )
            translator = state._create_translator(
                job.request_model,
                "zh",
                job.model_config,
                runtime=runtime,
                resolved_model_config=resolved_model,
            )

            self.assertIsInstance(translator, RuntimeBackedTranslator)
            snapshot = runtime.budget.snapshot()
            self.assertEqual(snapshot.request_limit, 150)
            self.assertEqual(str(snapshot.cost_limit_usd), "2")
            self.assertEqual(runtime.pricing.api_surface, "interactions-v1")
            self.assertEqual(runtime.pricing.input_usd_per_million, 1.5)
            self.assertEqual(
                runtime.audit_writer.directory,
                working,
            )

    def test_model_probe_uses_runtime_strict_schema_and_model_test_audit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = deep_merge(
                DEFAULT_CONFIG,
                {
                    "babeldoc": {"data_dir": directory},
                    "models": {
                        "gemini-1": {
                            "provider": "google",
                            "api_surface": "interactions-v1",
                            "api_key": "secret",
                            "model": "gemini-3.6-flash",
                        }
                    },
                },
            )
            state = AppState(config)
            adapter = OfflineAdapter()

            result = state.test_model(
                "gemini-1",
                "zh",
                adapter=adapter,
            )

            self.assertEqual(result["translation"], "你好")
            sent = adapter.sent_requests[0]
            self.assertEqual(sent.reasoning_level, "minimal")
            self.assertEqual(sent.response_format["mime_type"], "application/json")
            audit_files = list(
                (state.working_dir / "model-tests").glob(
                    "*/api_calls.jsonl"
                )
            )
            self.assertEqual(len(audit_files), 1)
            record = json.loads(
                audit_files[0].read_text(encoding="utf-8").splitlines()[0]
            )
            self.assertEqual(record["request_category"], "model_test")
            self.assertEqual(record["paragraph_ids"], [])

    def test_runtime_configuration_reports_missing_prices_clearly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = deep_merge(
                DEFAULT_CONFIG,
                {
                    "babeldoc": {"data_dir": directory},
                    "models": {
                        "deepseek": {
                            "base_url": "https://example.invalid/v1",
                            "api_key": "secret",
                            "model": "plain-model",
                        }
                    },
                },
            )
            state = AppState(config)

            with self.assertRaisesRegex(
                ValueError,
                "input_usd_per_million",
            ):
                state._create_translation_runtime(
                    Job(
                        pdf_id="doc-price",
                        object_key="paper.pdf",
                        file_name="paper.pdf",
                        request_model="deepseek",
                        target_language="zh",
                        model_config=None,
                        options={},
                        created_at=0,
                    ),
                    Path(directory) / "working",
                    adapter=OfflineAdapter(),
                )


if __name__ == "__main__":
    unittest.main()
