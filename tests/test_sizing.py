"""Tests for llm-d-semantic sizing studies and API persistence."""

from __future__ import annotations

import tempfile
import unittest
import zipfile
from io import BytesIO
from pathlib import Path
from unittest import mock

import storage
from app import app
from sizing import build_sizing_study, list_sizing_profiles, sizing_catalog


def _records(n: int = 60):
    shared = [11, 12, 13]
    return [
        {
            "timestamp": (index // 3) * 1000,
            "input_length": 2560,
            "output_length": 32,
            "hash_ids": shared + [1000 + index, 2000 + index],
        }
        for index in range(n)
    ]


def _manifest():
    return {
        "generator": "test",
        "n_requests": 60,
        "kv_cache_planning": {
            "bytes_per_block": 65536,
            "working_set_gib": 12.0,
            "points": [
                {
                    "gib": 40.0,
                    "cache_blocks": 655360,
                    "results": {"lru": {"hit_rate": 0.6}},
                }
            ],
        },
    }


class SizingStudyTests(unittest.TestCase):
    def test_profiles_are_ui_ready(self):
        profiles = list_sizing_profiles()
        self.assertGreaterEqual(len(profiles), 3)
        self.assertTrue(all(item["id"] and item["description"] for item in profiles))

    def test_build_study_sweeps_and_emits_llmd_yaml(self):
        result = build_sizing_study(
            _records(),
            manifest=_manifest(),
            options={
                "profile": "8b-h100-balanced",
                "cache_gib": 40,
                "target_ttft_p95_ms": 10000,
                "max_replicas": 4,
            },
        )
        self.assertEqual(result["schema_version"], "tracerator.sizing_result.v1")
        self.assertEqual(len(result["sweep"]), 4)
        self.assertTrue(result["recommendation"]["slo_met"])
        self.assertEqual(result["cache"]["source"], "tracerator_kv_cache_planning")
        yaml_text = result["llmd_config"]["yaml"]
        self.assertIn("latency-calculator: \"per-token\"", yaml_text)
        self.assertIn("block-size: 128", yaml_text)
        self.assertIn("kv-cache-size:", yaml_text)

    def test_analytical_envelope_reports_memory_and_phase_bounds(self):
        result = build_sizing_study(
            _records(),
            options={
                "profile": "8b-h100-balanced",
                "analytical_mode": "guardrail",
                "hardware_id": "h200-sxm-141gb",
                "tensor_parallel": 1,
                "max_replicas": 2,
                "target_ttft_p95_ms": 10000,
            },
        )
        envelope = result["analytical"]
        self.assertEqual(envelope["selection"]["hardware_id"], "h200-sxm-141gb")
        self.assertTrue(envelope["memory"]["fits"])
        self.assertIn(envelope["roofline"]["prefill"]["regime"], {"compute", "memory", "comms"})
        self.assertGreater(envelope["roofline"]["aggregate_output_tokens_s"], 0)
        self.assertEqual(result["inputs"]["analytical_mode"], "guardrail")

    def test_analytical_mode_seeds_latency_profile(self):
        result = build_sizing_study(
            _records(),
            options={
                "profile": "8b-h100-balanced",
                "analytical_mode": "analytical",
                "max_replicas": 1,
                "target_ttft_p95_ms": 10000,
            },
        )
        self.assertEqual(result["profile"]["prefill_overhead_ms"], 0)
        self.assertGreater(result["profile"]["inter_token_latency_ms"], 0)

    def test_catalog_includes_models_hardware_and_sources(self):
        catalog = sizing_catalog()
        self.assertGreaterEqual(len(catalog["models"]), 3)
        self.assertGreaterEqual(len(catalog["hardware"]), 4)
        self.assertTrue(all(item["source"].startswith("https://") for item in catalog["hardware"]))

    def test_replay_calibrates_profile(self):
        replay = {
            "latency_ms": {
                "time_to_first_token": {"p50": 200},
                "inter_token_latency": {"p50": 8},
            }
        }
        result = build_sizing_study(
            _records(),
            manifest=_manifest(),
            replay_result=replay,
            options={"calibration": "auto", "max_replicas": 2},
        )
        self.assertEqual(result["calibration"]["mode"], "replay")
        self.assertEqual(result["profile"]["inter_token_latency_ms"], 8)

    def test_invalid_profile_rejected(self):
        with self.assertRaises(ValueError):
            build_sizing_study(_records(), options={"profile": "missing"})

    def test_explicit_replay_calibration_requires_replay(self):
        with self.assertRaises(ValueError):
            build_sizing_study(_records(), options={"calibration": "replay"})


class SizingApiTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db = Path(self._tmpdir.name) / "api.db"
        self.runs_root = Path(self._tmpdir.name) / "runs"
        storage.init_db(self.db)
        self._db_patch = mock.patch.object(storage, "default_db_path", return_value=self.db)
        self._root_patch = mock.patch.object(storage, "default_runs_root", return_value=self.runs_root)
        self._db_patch.start()
        self._root_patch.start()
        self.client = app.test_client()

    def tearDown(self):
        self._db_patch.stop()
        self._root_patch.stop()
        self._tmpdir.cleanup()

    def test_sizing_route_persists_result_and_scene_uses_it(self):
        run = storage.create_run(
            name="Sizing",
            params={},
            db_path=self.db,
            runs_root=self.runs_root,
        )
        storage.write_trace_jsonl(run["id"], _records(), runs_root=self.runs_root)
        storage.update_run(run["id"], manifest=_manifest(), db_path=self.db)

        profiles = self.client.get("/sizing/profiles")
        self.assertEqual(profiles.status_code, 200)
        catalog = profiles.get_json()
        self.assertGreaterEqual(len(catalog["profiles"]), 3)
        self.assertGreaterEqual(len(catalog["models"]), 3)
        self.assertGreaterEqual(len(catalog["hardware"]), 4)

        response = self.client.post(
            f"/runs/{run['id']}/sizing",
            json={
                "profile": "8b-h100-balanced",
                "target_ttft_p95_ms": 10000,
                "max_replicas": 3,
            },
        )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertTrue(
            (self.runs_root / run["id"] / "sizing_result.json").exists()
        )
        self.assertTrue(
            (self.runs_root / run["id"] / "llmd-simulator.yaml").exists()
        )

        fetched = self.client.get(f"/runs/{run['id']}/sizing")
        self.assertEqual(fetched.status_code, 200)
        scene = self.client.get(f"/runs/{run['id']}/replay-scene")
        self.assertEqual(scene.status_code, 200, scene.get_data(as_text=True))
        self.assertTrue(scene.get_json()["scene"]["source"]["has_sizing"])

        bundle = self.client.get(f"/runs/{run['id']}/download")
        self.assertEqual(bundle.status_code, 200)
        with zipfile.ZipFile(BytesIO(bundle.data)) as archive:
            self.assertIn("sizing_result.json", archive.namelist())
            self.assertIn("llmd-simulator.yaml", archive.namelist())


if __name__ == "__main__":
    unittest.main()
