import io
import json
import unittest
import zipfile

from app import app, generate_trace_data


class WallClockWinScenarioTests(unittest.TestCase):
    def test_compare_page_route_serves_static_app(self):
        client = app.test_client()
        resp = client.get("/compare")

        try:
            self.assertEqual(resp.status_code, 200)
            self.assertIn(b"Trace Compare", resp.data)
            self.assertIn(b"manifest-files", resp.data)
            self.assertIn(b"vertical-chart", resp.data)
            self.assertIn(b"download-chart", resp.data)
            self.assertIn(b"Trace Generator", resp.data)
            self.assertIn(b"Trace Comparison", resp.data)
            self.assertIn(b"Print / save one-page PDF", resp.data)
            self.assertIn(b"size: letter", resp.data)
            self.assertIn(b"simulations for planning", resp.data)
        finally:
            resp.close()

    def test_scenario_trace_has_target_shape_and_block_integrity(self):
        reqs, manifest = generate_trace_data({
            "trace_scenario": "wall_clock_win",
            "scale": 1,
            "input_mult": 1,
            "output_mult": 0.8,
            "reuse_bias": 0.92,
            "seed": 42,
        })

        self.assertGreaterEqual(len(reqs), 4000)
        self.assertGreater(manifest["approx_cache_hit_ratio"], 0.75)
        self.assertGreater(manifest["reused_token_fraction"], 0.75)
        self.assertGreater(manifest["scenario_stats"]["shared_prefix_tokens_median"], 20_000)
        self.assertGreater(manifest["isl_distribution"]["8-16K"]["count"], 0)
        self.assertGreater(manifest["isl_distribution"]["16-32K"]["share"], 0.15)
        self.assertGreater(manifest["isl_distribution"]["32-64K"]["share"], 0.20)
        self.assertLess(manifest["isl_distribution"]["64-128K"]["share"], 0.25)
        self.assertGreater(manifest["isl_distribution"][">128K"]["count"], 0)
        self.assertLessEqual(manifest["median_output"], 70)
        self.assertGreaterEqual(manifest["max_concurrency"], 12)

        for req in reqs:
            expected_blocks = max(1, (req["input_length"] + 511) // 512)
            self.assertEqual(len(req["hash_ids"]), expected_blocks)

    def test_generate_endpoint_returns_valid_zip_for_scenario(self):
        client = app.test_client()
        resp = client.get("/generate?trace_scenario=wall_clock_win&scale=1&reuse_bias=0.92&output_mult=0.8")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content_type, "application/zip")
        with zipfile.ZipFile(io.BytesIO(resp.data)) as zf:
            self.assertIsNone(zf.testzip())
            manifest = json.loads(zf.read("manifest.json"))
            first_record = json.loads(zf.read("trace.jsonl").splitlines()[0])

        self.assertEqual(manifest["trace_scenario"]["id"], "wall_clock_win")
        self.assertEqual(manifest["trace_scenario"]["label"], "KV reuse stress scenario")
        self.assertIn("hash_ids", first_record)


if __name__ == "__main__":
    unittest.main()
