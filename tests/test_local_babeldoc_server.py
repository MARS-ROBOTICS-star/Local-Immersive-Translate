import asyncio
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from local_babeldoc_server.server import AppState


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def make_config(data_dir: Path) -> dict:
    return {
        "server": {
            "host": "127.0.0.1",
            "port": 0,
            "public_base_url": "",
            "token": "",
            "max_concurrent_jobs": 1,
        },
        "babeldoc": {
            "repo_path": str(data_dir / "BabelDOC"),
            "data_dir": str(data_dir),
        },
        "models": {},
    }


class FakeOpenAITranslator:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs


class AppStateRecoveryTest(unittest.TestCase):
    def test_recovers_completed_job_from_existing_output_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            state = AppState(make_config(data_dir))
            pdf_id = "6a1b90d4f91c4bea9753283d6ef30998"
            output_dir = data_dir / "outputs" / pdf_id
            output_dir.mkdir(parents=True)
            mono_path = output_dir / "source.zh.mono.pdf"
            dual_path = output_dir / "source.zh.dual.pdf"
            mono_path.write_bytes(b"%PDF-1.7\nmono\n")
            dual_path.write_bytes(b"%PDF-1.7\ndual\n")

            job = state.get_job_or_recover_finished(pdf_id)

            self.assertIsNotNone(job)
            assert job is not None
            self.assertEqual(job.status, "success")
            self.assertEqual(job.stage, "completed")
            self.assertEqual(job.progress, 100.0)
            self.assertEqual(job.translation_pdf_path, str(mono_path))
            self.assertEqual(job.dual_pdf_path, str(dual_path))
            self.assertIs(state.get_job(pdf_id), job)


class AppStateTranslatorTest(unittest.TestCase):
    def test_passes_thinking_option_to_babeldoc(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = make_config(Path(temp_dir))
            config["models"]["deepseek"] = {
                "base_url": "https://api.deepseek.example/v1",
                "api_key": "test-key",
                "model": "deepseek-chat",
                "thinking": "enabled",
            }
            state = AppState(config)

            babeldoc_module = types.ModuleType("babeldoc")
            babeldoc_module.__path__ = []
            translator_package = types.ModuleType("babeldoc.translator")
            translator_package.__path__ = []
            translator_module = types.ModuleType("babeldoc.translator.translator")
            translator_module.OpenAITranslator = FakeOpenAITranslator

            with patch.dict(
                sys.modules,
                {
                    "babeldoc": babeldoc_module,
                    "babeldoc.translator": translator_package,
                    "babeldoc.translator.translator": translator_module,
                },
            ):
                translator = state._create_translator("deepseek", "zh")

            self.assertEqual(translator.kwargs["thinking"], "enabled")


class AppStateTranslateEventsTest(unittest.TestCase):
    def test_returns_finish_result_without_waiting_for_stream_to_close(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state = AppState(make_config(Path(temp_dir)))
            expected_result = object()

            async def stream_stuck_after_finish(_config):
                yield {
                    "type": "finish",
                    "translate_result": expected_result,
                }
                await asyncio.Event().wait()

            async def consume():
                return await asyncio.wait_for(
                    state._consume_translate_events(
                        "938d971b76364e04b9939ec3dd01ae8c",
                        stream_stuck_after_finish,
                        SimpleNamespace(output_dir=temp_dir),
                    ),
                    timeout=0.1,
                )

            result = asyncio.run(consume())

            self.assertIs(result, expected_result)


class InstallerVersionTest(unittest.TestCase):
    def test_pins_babeldoc_v0_6_4_on_all_platforms(self) -> None:
        bash_installer = (
            PROJECT_ROOT / "scripts" / "install-local-backend.sh"
        ).read_text(encoding="utf-8")
        powershell_installer = (
            PROJECT_ROOT / "scripts" / "install-local-backend.ps1"
        ).read_text(encoding="utf-8")

        self.assertIn(
            'BABELDOC_REF="${BABELDOC_REF:-v0.6.4}"', bash_installer
        )
        self.assertIn(
            '[string]$BabelDocRef = "v0.6.4"', powershell_installer
        )


if __name__ == "__main__":
    unittest.main()
