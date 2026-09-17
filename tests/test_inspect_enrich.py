"""Tests for inspect_enrich view models and run API wiring."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import storage
from app import app
from inspect_enrich import (
    align_expected_vs_observed,
    build_arrival_timeline,
    build_events,
    build_narrative,
    build_overview,
    build_prefix_trace,
    build_waterfall,
    enrich_run,
)


def _sample_records(n=40):
    records = []
    shared = [10, 11, 12]
    for i in range(n):
        hashes = list(shared) + [1000 + i, 2000 + i]
        isl = len(hashes) * 512
        records.append({
            "timestamp": (i // 5) * 1000,
            "input_length": isl,
            "output_length": 50 + (i % 7),
            "hash_ids": hashes,
        })
    return records


class InspectEnrichTests(unittest.TestCase):
    def test_overview_integrity_and_prefix(self):
        records = _sample_records()
        overview = build_overview(records, {"integrity": {"schema": "mooncake_trace", "aiperf_ready": True}})
        self.assertEqual(overview["request_count"], 40)
        self.assertTrue(overview["integrity"]["ok"])
        self.assertGreater(overview["max_concurrency"], 1)
        self.assertGreater(overview["prefix"]["approx_cache_hit_ratio"], 0)
        self.assertIn("requests", overview["auto_summary"])

    def test_integrity_fails_on_bad_hash_ids(self):
        records = [{"timestamp": 0, "input_length": 1024, "output_length": 1, "hash_ids": [1]}]
        overview = build_overview(records)
        self.assertFalse(overview["integrity"]["ok"])
        self.assertEqual(overview["integrity"]["bad_rows"], 1)

    def test_timeline_waterfall_prefix(self):
        records = _sample_records()
        timeline = build_arrival_timeline(records)
        self.assertTrue(timeline["available"])
        self.assertGreater(timeline["max_burst"], 1)
        prefix = build_prefix_trace(records)
        self.assertTrue(prefix["available"])
        self.assertGreater(len(prefix["exchanges"]), 0)
        self.assertIn("verdict", prefix["exchanges"][0])
        wf = build_waterfall(records)
        self.assertTrue(wf["available"])
        self.assertGreater(len(wf["lanes"]), 0)

    def test_align_and_narrative(self):
        records = _sample_records()
        manifest = {
            "base_source": {"label": "Conversation"},
            "trace_scenario": {"label": "Standard"},
            "isl_distribution": {
                "<=1K": {"share": 0.0, "count": 0},
                "1-2K": {"share": 0.0, "count": 0},
                "2-4K": {"share": 1.0, "count": 40},
            },
            "n_requests": 40,
        }
        replay = {
            "artifact_type": "aiperf_replay_result",
            "summary": {"request_count": 40, "success_count": 38, "error_count": 2, "request_throughput": 1.2},
            "run": {"model": "Qwen/Qwen3-0.6B"},
            "latency_ms": {"time_to_first_token": {"p50": 100, "p95": 200, "avg": 120}},
            "observed_isl_distribution": {
                "2-4K": {"share": 0.8, "count": 32},
                "4-8K": {"share": 0.2, "count": 8},
            },
            "profile_records": {"error_types": {"timeout": 2}},
            "timeslices": {"available": False},
        }
        align = align_expected_vs_observed(manifest, replay)
        self.assertTrue(align["available"])
        self.assertTrue(len(align["isl_bins"]) > 0)
        overview = build_overview(records, manifest, replay)
        events = build_events(manifest=manifest, overview=overview, replay_result=replay, align=align)
        self.assertTrue(any(e["type"] == "replay" for e in events["events"]))
        narrative = build_narrative(records, manifest=manifest, overview=overview, replay_result=replay, align=align)
        self.assertIn("markdown", narrative)
        self.assertIn("Qwen", narrative["markdown"])

        bundle = enrich_run(records, params={"scale": 0.05}, manifest=manifest, replay_result=replay)
        self.assertIn("overview", bundle)
        self.assertIn("narrative", bundle)


class RunApiTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db = Path(self._tmpdir.name) / "api.db"
        self.runs_root = Path(self._tmpdir.name) / "runs"
        storage.init_db(self.db)
        self.client = app.test_client()
        self._db_patch = mock.patch.object(storage, "default_db_path", return_value=self.db)
        self._root_patch = mock.patch.object(storage, "default_runs_root", return_value=self.runs_root)
        self._db_patch.start()
        self._root_patch.start()

    def tearDown(self):
        self._db_patch.stop()
        self._root_patch.stop()
        self._tmpdir.cleanup()

    def test_create_generate_inspect_download(self):
        resp = self.client.post("/runs", json={
            "name": "API run",
            "source": "conversation",
            "scenario": "standard",
            "params": {
                "source": "conversation",
                "scenario": "standard",
                "scale": 0.01,
                "input_mult": 1,
                "output_mult": 1,
                "reuse_bias": 0.5,
                "new_sessions": 0.1,
                "modeled_mix": 0,
                "isl_profile": "empirical",
                "seed": 7,
            },
        })
        self.assertEqual(resp.status_code, 201)
        run = resp.get_json()
        run_id = run["id"]

        gen = self.client.post(f"/runs/{run_id}/generate", json={
            "name": "API run",
            "source": "conversation",
            "scenario": "standard",
            "scale": 0.01,
            "input_mult": 1,
            "output_mult": 1,
            "reuse_bias": 0.5,
            "new_sessions": 0.1,
            "modeled_mix": 0,
            "isl_profile": "empirical",
            "seed": 7,
        })
        self.assertEqual(gen.status_code, 200, gen.get_data(as_text=True))
        generated = gen.get_json()
        self.assertEqual(generated["status"], "generated")
        self.assertGreater(generated["request_count"], 0)
        self.assertTrue(storage.trace_path(run_id, self.runs_root).exists())

        inspect = self.client.get(f"/runs/{run_id}/inspect")
        self.assertEqual(inspect.status_code, 200)
        payload = inspect.get_json()
        self.assertIn("overview", payload["inspect"])
        self.assertTrue(payload["inspect"]["timeline"]["available"])
        self.assertIn("markdown", payload["inspect"]["narrative"])

        # Upload a minimal replay_result
        from io import BytesIO
        replay = {
            "artifact_type": "aiperf_replay_result",
            "schema_version": "tracerator.replay_result.v1",
            "summary": {"request_count": 10, "success_count": 10, "error_count": 0},
            "run": {"model": "test"},
            "latency_ms": {"time_to_first_token": {"p50": 11, "p95": 22, "avg": 12}},
            "observed_isl_distribution": generated["manifest"]["isl_distribution"],
            "profile_records": {"error_types": {}},
            "timeslices": {"available": False},
        }
        up = self.client.post(
            f"/runs/{run_id}/upload",
            data={"file": (BytesIO(json.dumps(replay).encode("utf-8")), "replay_result.json")},
            content_type="multipart/form-data",
        )
        self.assertEqual(up.status_code, 200, up.get_data(as_text=True))
        self.assertTrue(up.get_json()["has_replay"])

        dl = self.client.get(f"/runs/{run_id}/download")
        self.assertEqual(dl.status_code, 200)
        self.assertEqual(dl.mimetype, "application/zip")

        listed = self.client.get("/runs")
        self.assertEqual(listed.status_code, 200)
        self.assertGreaterEqual(len(listed.get_json()["runs"]), 1)

        deleted = self.client.delete(f"/runs/{run_id}")
        self.assertEqual(deleted.status_code, 200)


if __name__ == "__main__":
    unittest.main()
