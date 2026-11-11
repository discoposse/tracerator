import io
import json
import re
import tempfile
import unittest
import zipfile
from pathlib import Path

from app import app, generate_trace_data
from trace_sources import (
    _load_sageserve_request_csv,
    load_source_records,
    sources_for_api,
)


class WallClockWinScenarioTests(unittest.TestCase):
    def test_compare_page_route_serves_static_app(self):
        client = app.test_client()
        resp = client.get("/compare")

        try:
            self.assertEqual(resp.status_code, 200)
            self.assertIn(b"manifest-files", resp.data)
            self.assertIn(b"manifest-list", resp.data)
            self.assertIn(b"Add manifest or replay JSON", resp.data)
            self.assertIn(b"loadStoredComparePayload", resp.data)
            self.assertIn(b"function reindexTraces", resp.data)
            self.assertIn(b"tracerator_compare_payload", resp.data)
            self.assertIn(b"replay_result.json", resp.data)
            self.assertIn(b"aiperf_replay_result", resp.data)
            self.assertIn(b"Observed TTFT", resp.data)
            self.assertIn(b"data-action=\"remove\"", resp.data)
            self.assertIn(b"timestampForFilename", resp.data)
            self.assertIn(b"downloadFilename", resp.data)
            self.assertIn(b"carbon.css", resp.data)
            self.assertIn(b"vertical-chart", resp.data)
            self.assertIn(b"download-chart", resp.data)
            self.assertIn(b"Tracerator", resp.data)
            self.assertIn(b"Traces", resp.data)
            self.assertIn(b"Compare", resp.data)
            self.assertIn(b"workspace-run", resp.data)
            self.assertIn(b"Print / PDF", resp.data)
            self.assertIn(b"simulations for planning", resp.data)
        finally:
            resp.close()

    def test_workspace_home_and_run_page_expose_workflow_ui(self):
        client = app.test_client()
        home = client.get("/")
        try:
            self.assertEqual(home.status_code, 200)
            self.assertIn(b"new-run-btn", home.data)
            self.assertIn(b"Tracerator", home.data)
            self.assertIn(b"Runs", home.data)
            self.assertIn(b"workspace.css", home.data)
            self.assertIn(b"workspace.js", home.data)
        finally:
            home.close()

        run_page = client.get("/runs/demo/view")
        try:
            self.assertEqual(run_page.status_code, 200)
            self.assertIn(b"btn-generate", run_page.data)
            self.assertIn(b"btn-analyze", run_page.data)
            self.assertIn(b"btn-goto-replay", run_page.data)
            self.assertIn(b"btn-replay", run_page.data)
            self.assertIn(b"upload-drop", run_page.data)
            self.assertIn(b"step-inspect", run_page.data)
            self.assertIn(b"step-narrative", run_page.data)
            self.assertIn(b"run.js", run_page.data)
        finally:
            run_page.close()

    def test_run_page_exposes_inspect_tabs(self):
        client = app.test_client()
        resp = client.get("/run/demo")

        try:
            self.assertEqual(resp.status_code, 200)
            self.assertIn(b"tab-timeline", resp.data)
            self.assertIn(b"tab-prefix", resp.data)
            self.assertIn(b"tab-waterfall", resp.data)
            self.assertIn(b"tab-results", resp.data)
            self.assertIn(b"wall_clock_win", resp.data)
            self.assertIn(b"carbon.css", resp.data)
            self.assertIn(b"workspace.css", resp.data)
        finally:
            resp.close()

    def test_profiles_redirects_to_trace_pages(self):
        client = app.test_client()
        redirect = client.get("/profiles")
        try:
            self.assertEqual(redirect.status_code, 302)
            self.assertTrue(redirect.headers["Location"].endswith("/traces"))
        finally:
            redirect.close()

        page = client.get("/traces")
        try:
            self.assertEqual(page.status_code, 200)
            self.assertIn(b"Which Workload Should You Replay?", page.data)
            self.assertIn(b"Mooncake Conversation", page.data)
            self.assertIn(b"KV Reuse Stress", page.data)
            self.assertNotIn(b"benchmarking_workflow.png", page.data)
            self.assertNotIn(b"kv_hierarchy_latency.png", page.data)
            self.assertNotIn(b"Effective Efficiency", page.data)
        finally:
            page.close()

        trace = client.get("/traces/conversation")
        try:
            self.assertEqual(trace.status_code, 200)
            self.assertIn(b"trace_page.js", trace.data)
        finally:
            trace.close()

        download = client.get("/traces/conversation/trace.jsonl")
        try:
            self.assertEqual(download.status_code, 200)
            self.assertIn(b"hash_ids", download.data)
        finally:
            download.close()

    def test_static_ui_uses_phase_1a_visual_rules(self):
        repo = Path(__file__).resolve().parents[1]
        files = [
            path for path in (repo / "site").rglob("*")
            if path.suffix in {".css", ".html", ".js"} and path.is_file()
        ]
        self.assertTrue(files)
        for path in files:
            text = path.read_text(encoding="utf-8")
            relative = str(path.relative_to(repo))
            self.assertNotIn("Plex Mono", text, relative)
            self.assertNotIn("font-mono", text, relative)
            for match in re.finditer(r"border-radius:\s*([0-9.]+)px", text):
                self.assertLessEqual(float(match.group(1)), 8, relative)
            self.assertIsNone(re.search(r"border-radius:\s*[0-9.]+%", text), relative)
            stripped = re.sub(r"(?:pre|code|kbd)[^{]*\{[^}]*\}", "", text)
            self.assertNotIn("monospace", stripped, relative)

    def test_profile_figure_assets_are_gone(self):
        repo = Path(__file__).resolve().parents[1]
        for name in (
            "kv_hierarchy_latency.png",
            "benchmarking_workflow.png",
            "effective_efficiency_formula.png",
            "effective_efficiency_model.png",
        ):
            self.assertFalse((repo / "site" / name).exists(), name)
        self.assertFalse((repo / "Mooncake" / "Mooncake-FAST25.pdf").exists())

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
        disposition = resp.headers.get("Content-Disposition", "")
        self.assertRegex(
            disposition,
            r"purpose-built-kv-reuse-trace-kv-reuse-stress-scenario-empirical-source-\d{12}\.zip",
        )
        with zipfile.ZipFile(io.BytesIO(resp.data)) as zf:
            self.assertIsNone(zf.testzip())
            manifest = json.loads(zf.read("manifest.json"))
            first_record = json.loads(zf.read("trace.jsonl").splitlines()[0])

        self.assertEqual(manifest["trace_scenario"]["id"], "wall_clock_win")
        self.assertEqual(manifest["trace_scenario"]["label"], "KV reuse stress scenario")
        self.assertIn("hash_ids", first_record)

    def test_sageserve_sources_are_exposed(self):
        sources = {source["id"]: source for source in sources_for_api()}

        self.assertIn("sageserve-enterprise", sources)
        self.assertIn("sageserve-code", sources)
        self.assertIn("sageserve-conversation", sources)
        self.assertEqual(sources["sageserve-code"]["mode"], "sageserve_distribution_csv")
        self.assertIn("deterministic", sources["sageserve-code"]["cache_fidelity"])

    def test_sageserve_distribution_loader_preserves_shape_and_block_integrity(self):
        from trace_sources import source_by_id
        if not source_by_id("sageserve-code").available:
            self.skipTest("SageServe distribution CSVs are not in this checkout")
        code = load_source_records("sageserve-code", max_records=128)
        conversation = load_source_records("sageserve-conversation", max_records=128)

        self.assertEqual(code[0]["timestamp"], 0)
        self.assertEqual(conversation[0]["timestamp"], 0)
        self.assertGreater(code[-1]["timestamp"], code[0]["timestamp"])
        self.assertGreater(conversation[-1]["timestamp"], conversation[0]["timestamp"])
        self.assertGreater(
            sorted(r["input_length"] for r in code)[len(code) // 2],
            sorted(r["input_length"] for r in conversation)[len(conversation) // 2],
        )
        for record in code + conversation:
            expected_blocks = max(1, (record["input_length"] + 511) // 512)
            self.assertEqual(len(record["hash_ids"]), expected_blocks)

    def test_sageserve_request_csv_loader_maps_enterprise_fields_to_prefixes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "week-dp.csv"
            path.write_text(
                "request_id,batch_id,client_tenant,request_type,scenario,sla,utility,regions,model_type,workload_type,application_id,arrival_timestamp,batch_size,prompt_size,token_size\n"
                "1,-1,0,2,prod-chat,10,1.0,012,A,prod,0,0.125,1,1024,64\n"
                "2,-1,0,2,prod-chat,10,1.0,012,A,prod,0,0.125,1,1536,96\n"
                "3,-1,1,2,dev-code,3600,0.2,210,B,dev,0,2.5,1,2048,128\n",
                encoding="utf-8",
            )

            records = _load_sageserve_request_csv(path, "sageserve-enterprise-test")

        self.assertEqual([record["timestamp"] for record in records], [125, 125, 2500])
        self.assertEqual(records[0]["hash_ids"][1], records[1]["hash_ids"][1])
        self.assertNotEqual(records[0]["hash_ids"][1], records[2]["hash_ids"][1])
        self.assertEqual(records[2]["workload_type"], "dev")
        for record in records:
            expected_blocks = max(1, (record["input_length"] + 511) // 512)
            self.assertEqual(len(record["hash_ids"]), expected_blocks)


if __name__ == "__main__":
    unittest.main()
