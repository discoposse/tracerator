import json
import math
import tempfile
import unittest
from pathlib import Path

from kv_cache_planning import cache_bytes_per_token
from lightinferra_golden import (
    DEFAULT_LOGICAL_BLOCK_TOKENS,
    GOLDEN_MODELS,
    build_case,
    validate_bundle,
    write_bundle,
)
from lightinferra_golden_results import compare_case_results, load_turn2_ttft


class LightInferraGoldenTraceTests(unittest.TestCase):
    @staticmethod
    def _write_aiperf_records(path, turn1_ttft, turn2_ttft, input_tokens):
        path.mkdir(parents=True)
        rows = [
            {
                "metadata": {"session_num": 0, "benchmark_phase": "profiling"},
                "metrics": {
                    "input_sequence_length": {"value": input_tokens - 32, "unit": "tokens"},
                    "time_to_first_token": {"value": turn1_ttft, "unit": "ms"},
                },
                "error": None,
            },
            {
                "metadata": {"session_num": 1, "benchmark_phase": "profiling"},
                "metrics": {
                    "input_sequence_length": {"value": input_tokens, "unit": "tokens"},
                    "time_to_first_token": {"value": turn2_ttft, "unit": "ms"},
                },
                "error": None,
            },
        ]
        (path / "profile_export.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in rows)
        )

    def test_published_model_kv_geometry_uses_config_fields(self):
        qwen = cache_bytes_per_token("qwen2.5-7b-instruct-1m")
        deepseek = cache_bytes_per_token("deepseek-r1-distill-llama-70b")
        llama4 = cache_bytes_per_token("llama4-scout-17b-16e")

        self.assertEqual(qwen["bytes_per_token"], 28 * 2 * 4 * 128 * 2)
        self.assertEqual(deepseek["bytes_per_token"], 80 * 2 * 8 * 128 * 2)
        self.assertEqual(llama4["bytes_per_token"], 48 * 2 * 8 * 128 * 2)

    def test_turn2_reuses_only_complete_logical_prefix_blocks(self):
        model = GOLDEN_MODELS["qwen2.5-7b"]
        context_tokens = 1_001
        turn1, turn2, combined, manifest = build_case(
            model=model,
            context_tokens=context_tokens,
            repetitions=1,
            logical_block_tokens=DEFAULT_LOGICAL_BLOCK_TOKENS,
            physical_page_tokens=16,
            resume_tail_tokens=32,
            output_tokens=8,
            seed=42,
        )

        self.assertEqual(len(turn1), 1)
        self.assertEqual(len(turn2), 1)
        self.assertEqual(len(combined), 2)
        self.assertEqual(len(combined[0]["hash_ids"]), math.ceil(context_tokens / 512))
        self.assertEqual(len(combined[1]["hash_ids"]), math.ceil((context_tokens + 32) / 512))
        self.assertEqual(combined[0]["hash_ids"][0], combined[1]["hash_ids"][0])
        self.assertNotEqual(combined[0]["hash_ids"][1], combined[1]["hash_ids"][1])
        self.assertEqual(manifest["workload"]["expected_shared_prefix_tokens"], 512)

    def test_headline_bundle_has_four_reference_cases_and_valid_rows(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "golden"
            manifest = write_bundle(root, suite="headline", repetitions=2)
            validation = validate_bundle(root)

            self.assertEqual(manifest["case_count"], 4)
            self.assertEqual(validation, {"cases": 4, "rows": 16})
            self.assertTrue(all(case["reference_point"] for case in manifest["cases"]))

            qwen_case = root / "qwen2.5-7b" / "1010000"
            case_manifest = json.loads((qwen_case / "case_manifest.json").read_text())
            rows = [json.loads(line) for line in (qwen_case / "trace.jsonl").read_text().splitlines()]
            storage = [
                json.loads(line)
                for line in (qwen_case / "kv_io_summary.jsonl").read_text().splitlines()
            ]

            self.assertEqual(case_manifest["published_reference"]["published_speedup_x"], 286)
            self.assertGreater(case_manifest["workload"]["expected_turn2_prefix_reuse_fraction"], 0.999)
            self.assertEqual(len(rows), 4)
            self.assertEqual(len(storage), 4)
            self.assertEqual(storage[1]["operation"], "read")
            self.assertIn("upper bound", storage[1]["coverage"])

    def test_bundle_refuses_to_overwrite_nonempty_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "golden"
            root.mkdir()
            (root / "keep.txt").write_text("user data")

            with self.assertRaises(FileExistsError):
                write_bundle(root)

            self.assertEqual((root / "keep.txt").read_text(), "user data")

    def test_result_comparison_selects_turn2_instead_of_aggregate_ttft(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            bundle = root / "bundle"
            write_bundle(
                bundle,
                suite="headline",
                model_ids=["qwen2.5-7b"],
                repetitions=1,
            )
            case = bundle / "qwen2.5-7b" / "1010000" / "case_manifest.json"
            regen = root / "regen"
            remote_kv = root / "remote_kv"
            self._write_aiperf_records(regen, 400_000, 300_000, 1_010_032)
            self._write_aiperf_records(remote_kv, 400_000, 1_000, 1_010_032)

            self.assertEqual(
                load_turn2_ttft(
                    regen,
                    repetitions=1,
                    expected_input_tokens=1_010_032,
                ),
                [300_000],
            )
            result = compare_case_results(
                case,
                regen,
                remote_kv,
                remote_kv_read_bytes=123_456,
            )

            self.assertEqual(result["turn2_ttft_ms"]["cache_regen"]["p95"], 300_000)
            self.assertEqual(result["turn2_ttft_ms"]["remote_kv"]["p95"], 1_000)
            self.assertEqual(result["observed_speedup_x"]["p95"], 300)
            self.assertEqual(result["functional_evidence"]["status"], "valid")


if __name__ == "__main__":
    unittest.main()
