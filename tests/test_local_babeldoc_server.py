import asyncio
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from local_babeldoc_server.server import AppState
from local_babeldoc_server.server import TranslationCompletenessError
from local_babeldoc_server.server import audit_translation_completion
from local_babeldoc_server.server import ensure_translation_complete
from local_babeldoc_server.server import load_config
from local_babeldoc_server.server import find_zotero_ancestor
from local_babeldoc_server.server import is_watched_process_alive
from local_babeldoc_server.server import start_zotero_parent_watchdog


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class FakeProcess:
    def __init__(
        self,
        pid: int,
        *,
        name: str,
        cmdline: tuple[str, ...],
        create_time: float,
        parents: tuple["FakeProcess", ...] = (),
    ) -> None:
        self.pid = pid
        self._name = name
        self._cmdline = cmdline
        self._create_time = create_time
        self._parents = parents

    def name(self) -> str:
        return self._name

    def cmdline(self) -> list[str]:
        return list(self._cmdline)

    def create_time(self) -> float:
        return self._create_time

    def parents(self) -> list["FakeProcess"]:
        return list(self._parents)


class EventServer:
    def __init__(self) -> None:
        self.shutdown_called = threading.Event()

    def shutdown(self) -> None:
        self.shutdown_called.set()


class ZoteroParentWatchdogTest(unittest.TestCase):
    def test_finds_zotero_ancestor_by_process_identity(self) -> None:
        zotero = FakeProcess(
            122529,
            name="zotero-bin",
            cmdline=("/usr/lib/zotero/zotero-bin", "-app"),
            create_time=1785585656.25,
        )
        uv = FakeProcess(
            17667,
            name="uv",
            cmdline=("uv", "run", "python"),
            create_time=1785502605.5,
        )
        server = FakeProcess(
            17671,
            name="python3",
            cmdline=("python3", "server.py"),
            create_time=1785502606.0,
            parents=(uv, zotero),
        )

        watched = find_zotero_ancestor(server)

        self.assertEqual(watched.pid, 122529)
        self.assertEqual(watched.create_time, 1785585656.25)

    def test_manual_backend_without_zotero_ancestor_is_not_managed(self) -> None:
        shell = FakeProcess(
            500,
            name="bash",
            cmdline=("bash",),
            create_time=100.0,
        )
        server_process = FakeProcess(
            501,
            name="python3",
            cmdline=("python3", "server.py"),
            create_time=101.0,
            parents=(shell,),
        )
        server = EventServer()

        thread = start_zotero_parent_watchdog(
            server,
            current_process=server_process,
            poll_interval=0.001,
        )

        self.assertIsNone(thread)
        self.assertFalse(server.shutdown_called.is_set())

    def test_pid_reuse_does_not_count_as_the_same_owner(self) -> None:
        watched = SimpleNamespace(pid=42, create_time=100.0)
        replacement = FakeProcess(
            42,
            name="zotero-bin",
            cmdline=("/usr/lib/zotero/zotero-bin",),
            create_time=200.0,
        )

        alive = is_watched_process_alive(
            watched,
            process_factory=lambda _pid: replacement,
        )

        self.assertFalse(alive)

    def test_watchdog_shuts_down_server_when_zotero_owner_disappears(self) -> None:
        zotero = FakeProcess(
            42,
            name="zotero-bin",
            cmdline=("/usr/lib/zotero/zotero-bin",),
            create_time=100.0,
        )
        server_process = FakeProcess(
            43,
            name="python3",
            cmdline=("python3", "server.py"),
            create_time=101.0,
            parents=(zotero,),
        )
        reused_pid = FakeProcess(
            42,
            name="unrelated-process",
            cmdline=("unrelated-process",),
            create_time=200.0,
        )
        server = EventServer()

        thread = start_zotero_parent_watchdog(
            server,
            current_process=server_process,
            process_factory=lambda _pid: reused_pid,
            poll_interval=0.001,
        )

        self.assertIsNotNone(thread)
        self.assertTrue(server.shutdown_called.wait(0.2))


class ServerEntrypointTest(unittest.TestCase):
    def test_direct_script_start_works_outside_project_directory(self) -> None:
        server_script = PROJECT_ROOT / "local_babeldoc_server" / "server.py"
        with tempfile.TemporaryDirectory() as temp_dir:
            result = subprocess.run(
                [sys.executable, str(server_script), "--help"],
                cwd=temp_dir,
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Local BabelDOC server for Zotero", result.stdout)


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


class StructureRepairConfigTest(unittest.TestCase):
    def test_default_config_enables_all_structure_repairs(self) -> None:
        babeldoc = load_config(None)["babeldoc"]

        self.assertIs(babeldoc["enable_native_table_translation"], True)
        self.assertIs(babeldoc["enable_table_ocr"], True)
        self.assertIs(babeldoc["preserve_references"], True)
        self.assertIs(babeldoc["preserve_toc_layout"], True)
        self.assertIs(babeldoc["enable_translation_quality_guard"], True)
        self.assertEqual(babeldoc["translation_retry_chunk_sizes"], [700, 350])
        self.assertIs(babeldoc["fail_on_unresolved_translation"], True)

    def test_table_ocr_runtime_is_lazy_reused_and_can_be_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = make_config(Path(temp_dir))
            config["babeldoc"]["enable_table_ocr"] = True
            state = AppState(config)

            first = state._get_table_ocr_runtime()
            second = state._get_table_ocr_runtime()

            self.assertIs(first, second)
            self.assertIsNone(first._engine_instance)

        with tempfile.TemporaryDirectory() as temp_dir:
            config = make_config(Path(temp_dir))
            config["babeldoc"]["enable_table_ocr"] = False
            state = AppState(config)

            self.assertIsNone(state._get_table_ocr_runtime())


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


class TranslationCompletionAuditTest(unittest.TestCase):
    def test_detects_empty_and_unchanged_attempted_translations_but_skips_references(self) -> None:
        tracking = {
            "page": [
                {
                    "paragraph": [
                        {
                            "input": "Mobile robots navigate autonomously.",
                            "output": "",
                        },
                        {
                            "input": (
                                "Sensor fusion combines measurements from several "
                                "devices to improve estimation accuracy."
                            ),
                            "output": (
                                "Sensor fusion combines measurements from several "
                                "devices to improve estimation accuracy."
                            ),
                        },
                        {"input": "REFERENCES", "output": None},
                        {
                            "input": "[1] D. Di Paola, An autonomous mobile robot.",
                            "output": None,
                        },
                    ]
                }
            ],
            "cross_page": [],
            "cross_column": [],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            tracking_path = Path(temp_dir) / "translate_tracking.json"
            tracking_path.write_text(__import__("json").dumps(tracking), encoding="utf-8")

            audit = audit_translation_completion(Path(temp_dir), None, "zh")

        self.assertEqual(audit.attempted_count, 2)
        self.assertEqual(audit.empty_replacement_count, 1)
        self.assertEqual(audit.unresolved_count, 2)
        self.assertEqual(audit.reference_excluded_count, 2)

    def test_merges_local_recovery_counts_without_double_counting_unresolved_rows(self) -> None:
        tracking = {
            "page": [
                {
                    "paragraph": [
                        {
                            "input": "A paragraph that failed translation completely.",
                            "output": (
                                "A paragraph that failed translation completely."
                            ),
                        },
                        {
                            "input": "A recovered paragraph.",
                            "output": "一个已恢复的段落。",
                        },
                    ]
                }
            ],
            "cross_page": [],
            "cross_column": [],
        }
        local_quality = {
            "recovered_count": 1,
            "unresolved_count": 1,
            "unresolved": [
                {
                    "paragraph_id": "p1",
                    "source_preview": "A paragraph that failed translation completely.",
                    "reasons": ["empty_target"],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            tracking_path = Path(temp_dir) / "translate_tracking.json"
            tracking_path.write_text(__import__("json").dumps(tracking), encoding="utf-8")

            audit = audit_translation_completion(
                Path(temp_dir), local_quality, "zh"
            )

        self.assertEqual(audit.recovered_count, 1)
        self.assertEqual(audit.unresolved_count, 1)

    def test_unresolved_audit_cannot_be_marked_as_success(self) -> None:
        audit = SimpleNamespace(
            unresolved_count=3,
            empty_replacement_count=0,
            protected_token_mismatch_count=0,
        )

        with self.assertRaisesRegex(
            TranslationCompletenessError,
            "3 unresolved translatable paragraphs",
        ):
            ensure_translation_complete(audit, fail_on_unresolved=True)


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

    def test_installs_and_prewarms_table_ocr_dependency_on_all_platforms(self) -> None:
        bash_installer = (
            PROJECT_ROOT / "scripts" / "install-local-backend.sh"
        ).read_text(encoding="utf-8")
        powershell_installer = (
            PROJECT_ROOT / "scripts" / "install-local-backend.ps1"
        ).read_text(encoding="utf-8")
        requirements = (
            PROJECT_ROOT / "local_babeldoc_server" / "requirements.txt"
        ).read_text(encoding="utf-8")

        self.assertIn("rapidocr>=3.4,<4", requirements)
        self.assertIn("local_babeldoc_server/requirements.txt", bash_installer)
        self.assertIn("from rapidocr import RapidOCR; RapidOCR()", bash_installer)
        self.assertIn("local_babeldoc_server\\requirements.txt", powershell_installer)
        self.assertIn("from rapidocr import RapidOCR; RapidOCR()", powershell_installer)


if __name__ == "__main__":
    unittest.main()
