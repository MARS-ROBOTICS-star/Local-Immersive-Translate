import tempfile
import unittest
from pathlib import Path

from local_babeldoc_server.server import AppState


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


if __name__ == "__main__":
    unittest.main()
