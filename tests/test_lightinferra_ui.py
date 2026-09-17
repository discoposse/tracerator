import io
import json
import math
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import storage
from app import app, generate_trace_data
from trace_sources import sources_for_api


class LightInferraGoldenUiTests(unittest.TestCase):
    def test_golden_generator_is_an_available_trace_source(self):
        sources = {source["id"]: source for source in sources_for_api()}

        golden = sources["lightinferra-golden"]
        self.assertTrue(golden["available"])
        self.assertEqual(golden["mode"], "lightinferra_golden")
        self.assertEqual(golden["family"], "External KV")
        self.assertIn("Turn-1", golden["description"])

    def test_options_and_run_page_expose_golden_controls(self):
        client = app.test_client()
        options_response = client.get("/sources/lightinferra-golden/options")
        legacy_options = client.get("/sources/lightinferra-ddn-golden/options")
        try:
            self.assertEqual(options_response.status_code, 200)
            self.assertEqual(legacy_options.status_code, 200)
            options = options_response.get_json()
            self.assertEqual(legacy_options.get_json()["source_id"], "lightinferra-golden")
            self.assertEqual(options["defaults"]["context_tokens"], 1_010_000)
            self.assertEqual(
                {model["id"] for model in options["models"]},
                {"qwen2.5-7b", "deepseek-r1-70b", "llama4-scout-17b"},
            )
        finally:
            options_response.close()
            legacy_options.close()

        run_page = client.get("/runs/demo/view")
        try:
            self.assertEqual(run_page.status_code, 200)
            self.assertIn(b"lightinferra-config", run_page.data)
            self.assertIn(b"lightinferra-model", run_page.data)
            self.assertIn(b"lightinferra-context", run_page.data)
            self.assertIn(b"Physical page tokens", run_page.data)
        finally:
            run_page.close()

    def test_generate_trace_data_builds_selected_golden_case(self):
        records, manifest = generate_trace_data({
            "source": "lightinferra-ddn-golden",  # legacy alias still accepted
            "lightinferra_suite": "headline",
            "lightinferra_model": "qwen2.5-7b",
            "lightinferra_context_tokens": 409_600,
            "lightinferra_repetitions": 2,
            "lightinferra_physical_page_tokens": 16,
            "lightinferra_resume_tail_tokens": 32,
            "lightinferra_output_tokens": 8,
            "seed": 20260310,
        })

        self.assertEqual(len(records), 4)
        self.assertEqual(manifest["base_source"]["id"], "lightinferra-golden")
        self.assertEqual(manifest["trace_scenario"]["id"], "lightinferra_golden")
        golden = manifest["lightinferra_golden"]
        self.assertEqual(golden["workload"]["context_tokens"], 409_600)
        self.assertEqual(golden["published_reference"]["published_speedup_x"], 102)
        self.assertEqual(manifest["params"]["kv_model"], "qwen2.5-7b-instruct-1m")
        for record in records:
            self.assertEqual(
                len(record["hash_ids"]),
                math.ceil(record["input_length"] / 512),
            )

    def test_workspace_download_keeps_phase_and_storage_artifacts(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            os.environ,
            {
                "TRACERATOR_DB": str(Path(tmpdir) / "tracerator.db"),
                "TRACERATOR_RUNS_ROOT": str(Path(tmpdir) / "runs"),
            },
        ):
            storage.init_db()
            client = app.test_client()
            created = client.post(
                "/runs",
                json={
                    "name": "Golden UI test",
                    "source": "lightinferra-golden",
                    "params": {"source": "lightinferra-golden"},
                },
            )
            self.assertEqual(created.status_code, 201)
            run_id = created.get_json()["id"]
            generated = client.post(
                f"/runs/{run_id}/generate",
                json={
                    "name": "Golden UI test",
                    "source": "lightinferra-golden",
                    "lightinferra_suite": "curve",
                    "lightinferra_model": "qwen2.5-7b",
                    "lightinferra_context_tokens": 800,
                    "lightinferra_repetitions": 2,
                    "lightinferra_physical_page_tokens": 16,
                    "lightinferra_resume_tail_tokens": 32,
                    "lightinferra_output_tokens": 8,
                    "seed": 99,
                },
            )
            self.assertEqual(generated.status_code, 200)
            self.assertEqual(generated.get_json()["request_count"], 4)

            inspection = client.get(f"/runs/{run_id}/inspect")
            self.assertEqual(inspection.status_code, 200)
            self.assertTrue(inspection.get_json()["inspect"]["overview"]["integrity"]["ok"])

            download = client.get(f"/runs/{run_id}/download")
            self.assertEqual(download.status_code, 200)
            with zipfile.ZipFile(io.BytesIO(download.data)) as bundle:
                names = set(bundle.namelist())
                self.assertTrue({
                    "trace.jsonl",
                    "turn1_warm.jsonl",
                    "turn2_resume.jsonl",
                    "case_manifest.json",
                    "kv_io_summary.jsonl",
                }.issubset(names))
                case_manifest = json.loads(bundle.read("case_manifest.json"))
                turn1 = bundle.read("turn1_warm.jsonl").splitlines()
                turn2 = bundle.read("turn2_resume.jsonl").splitlines()
                storage_ops = bundle.read("kv_io_summary.jsonl").splitlines()

            self.assertEqual(case_manifest["case_id"], "qwen2.5-7b-800")
            self.assertEqual(len(turn1), 2)
            self.assertEqual(len(turn2), 2)
            self.assertEqual(len(storage_ops), 4)


if __name__ == "__main__":
    unittest.main()
