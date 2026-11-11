"""Trace Card contract, artifact, and API coverage."""

from __future__ import annotations

from io import BytesIO
import tempfile
import unittest
from pathlib import Path
from unittest import mock
import zipfile

import storage
from app import app
from trace_cards import build_run_trace_card, build_source_trace_card
from trace_sources import TraceSource


def _records():
    return [
        {"timestamp": 0, "input_length": 1024, "output_length": 32, "hash_ids": [1, 2]},
        {"timestamp": 0, "input_length": 1536, "output_length": 48, "hash_ids": [1, 2, 3]},
        {"timestamp": 1000, "input_length": 4096, "output_length": 64, "hash_ids": list(range(10, 18))},
    ]


class TraceCardContractTests(unittest.TestCase):
    def test_run_card_has_stable_identity_shape_and_metrics(self):
        card = build_run_trace_card(
            "run-123",
            "Agent replay",
            _records(),
            {
                "n_requests": 3,
                "max_concurrency": 2,
                "base_source": {
                    "id": "toolagent",
                    "label": "Mooncake Tool & Agent",
                    "family": "Mooncake",
                    "description": "Agent and tool-use traffic.",
                    "mode": "mooncake",
                    "cache_fidelity": "block hash_ids",
                    "source_url": "https://example.test/original",
                },
                "integrity": {"aiperf_ready": True},
            },
        )
        self.assertEqual(card["schema_version"], "tracerator.trace_card.v2")
        second = build_run_trace_card(
            "run-123",
            "Agent replay",
            _records(),
            {
                "n_requests": 3,
                "max_concurrency": 2,
                "base_source": {
                    "id": "toolagent",
                    "family": "Mooncake",
                    "description": "Agent and tool-use traffic.",
                    "source_url": "https://example.test/original",
                },
            },
        )
        self.assertEqual(card["charts"]["rpm"], second["charts"]["rpm"])
        self.assertEqual(len(card["charts"]["input_shares"]), 9)
        self.assertEqual(card["stats"]["request_count"], 3)
        self.assertEqual(card["stats"]["max_concurrency"], 2)
        self.assertGreater(card["stats"]["cache_hit_ratio"], 0)
        self.assertIn("AGENTIC", card["traits"])
        self.assertEqual(card["links"]["original"], "https://example.test/original")

    @mock.patch("trace_cards.load_source_records", return_value=_records())
    def test_source_card_keeps_original_provenance(self, _load):
        source = TraceSource(
            id="example",
            label="Example Trace",
            family="Research",
            path=Path("/tmp/example.jsonl"),
            mode="mooncake",
            description="A compact reference trace.",
            cache_fidelity="block hash_ids",
            available=True,
            source_url="https://example.test/dataset",
            source_label="Original dataset",
            license="Apache-2.0",
        )
        card = build_source_trace_card(source)
        self.assertTrue(card["availability"]["available"])
        self.assertEqual(card["provenance"]["label"], "Original dataset")
        self.assertEqual(card["provenance"]["license"], "Apache-2.0")
        self.assertEqual(card["links"]["original"], "https://example.test/dataset")

    def test_browser_renderer_draws_real_charts_without_a_fingerprint(self):
        source = Path(__file__).resolve().parents[1] / "site" / "js" / "trace_card.js"
        javascript = source.read_text(encoding="utf-8")
        self.assertIn("TraceratorCharts.area", javascript)
        self.assertIn("TraceratorCharts.bars", javascript)
        self.assertIn("Open trace", javascript)
        self.assertNotIn("fingerprint", javascript)
        self.assertNotIn("Download PNG", javascript)
        self.assertNotIn("Plex Mono", javascript)


class TraceCardApiTests(unittest.TestCase):
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

    def test_generated_run_persists_and_bundles_trace_card(self):
        created = self.client.post(
            "/runs",
            json={
                "name": "Card run",
                "source": "conversation",
                "scenario": "standard",
                "params": {"source": "conversation", "scenario": "standard", "scale": 0.01},
            },
        ).get_json()
        run_id = created["id"]
        response = self.client.post(
            f"/runs/{run_id}/generate",
            json={
                "name": "Collector edition",
                "source": "conversation",
                "scenario": "standard",
                "scale": 0.01,
                "input_mult": 1,
                "output_mult": 1,
                "reuse_bias": 0.5,
                "new_sessions": 0,
                "isl_profile": "empirical",
                "seed": 7,
            },
        )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        card_response = self.client.get(f"/runs/{run_id}/trace-card")
        self.assertEqual(card_response.status_code, 200)
        card = card_response.get_json()["card"]
        self.assertEqual(card["title"], "Collector edition")
        self.assertGreater(card["stats"]["request_count"], 0)
        self.assertTrue((self.runs_root / run_id / "trace_card.json").exists())

        bundle = self.client.get(f"/runs/{run_id}/download")
        self.assertEqual(bundle.status_code, 200)
        with zipfile.ZipFile(BytesIO(bundle.data)) as archive:
            self.assertIn("trace_card.json", archive.namelist())

    def test_draft_run_has_no_card(self):
        run = storage.create_run(name="Draft", db_path=self.db, runs_root=self.runs_root)
        response = self.client.get(f"/runs/{run['id']}/trace-card")
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
