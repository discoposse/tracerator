import json
import tempfile
import unittest
from pathlib import Path

from aiperf_results import normalize_aiperf_run
from app import app


class AiperfResultImportTests(unittest.TestCase):
    def test_normalizes_existing_aiperf_artifact_directory(self):
        repo = Path(__file__).resolve().parents[1]
        artifact_dir = repo / "artifacts" / "Qwen_Qwen3-0.6B-openai-chat-fixed_schedule"
        if not artifact_dir.is_dir():
            self.skipTest("local AIPerf artifact is not in this checkout")

        result = normalize_aiperf_run(artifact_dir)

        self.assertEqual(result["artifact_type"], "aiperf_replay_result")
        self.assertEqual(result["schema_version"], "tracerator.replay_result.v1")
        self.assertEqual(result["run"]["model"], "Qwen/Qwen3-0.6B")
        self.assertEqual(result["summary"]["request_count"], 48.0)
        self.assertEqual(result["summary"]["error_count"], 2.0)
        self.assertGreater(result["summary"]["error_rate"], 0)
        self.assertGreater(result["latency_ms"]["time_to_first_token"]["p95"], 0)
        self.assertGreater(result["latency_ms"]["request_latency"]["p50"], 0)
        self.assertEqual(result["observed_isl_distribution"]["16-32K"]["count"], 13)
        self.assertTrue(result["timeslices"]["available"])

    def test_import_route_returns_replay_result(self):
        repo = Path(__file__).resolve().parents[1]
        artifact_dir = repo / "artifacts" / "Qwen_Qwen3-0.6B-openai-chat-fixed_schedule"
        if not artifact_dir.is_dir():
            self.skipTest("local AIPerf artifact is not in this checkout")
        client = app.test_client()

        resp = client.post("/aiperf/import", json={"artifact_dir": str(artifact_dir)})

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["artifact_type"], "aiperf_replay_result")
        self.assertEqual(data["run"]["model"], "Qwen/Qwen3-0.6B")

    def test_import_route_rejects_missing_directory(self):
        client = app.test_client()

        resp = client.post("/aiperf/import", json={"artifact_dir": "/tmp/not-a-real-aiperf-run"})

        self.assertEqual(resp.status_code, 400)
        self.assertIn("missing", resp.get_json()["error"])

    def test_cli_output_shape_can_be_saved(self):
        repo = Path(__file__).resolve().parents[1]
        artifact_dir = repo / "artifacts" / "Qwen_Qwen3-0.6B-openai-chat-fixed_schedule"
        if not artifact_dir.is_dir():
            self.skipTest("local AIPerf artifact is not in this checkout")
        result = normalize_aiperf_run(artifact_dir)

        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "replay_result.json"
            output.write_text(json.dumps(result), encoding="utf-8")
            loaded = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(loaded["artifact_type"], "aiperf_replay_result")


if __name__ == "__main__":
    unittest.main()
