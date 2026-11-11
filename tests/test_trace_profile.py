"""Whole-trace profile and trace page contract."""

import json
import unittest
from pathlib import Path

from app import app
from trace_cards import list_builtin_trace_cards
from trace_profile import assign_parents, build_profile


class TraceProfileTests(unittest.TestCase):
    def test_parent_is_the_longest_shared_prefix(self):
        records = [
            {"timestamp": 0, "input_length": 2048, "output_length": 8, "hash_ids": [1, 2, 3, 4]},
            {"timestamp": 10, "input_length": 2048, "output_length": 8, "hash_ids": [1, 2, 9, 10]},
            {"timestamp": 20, "input_length": 1024, "output_length": 8, "hash_ids": [1, 2, 9, 11]},
            {"timestamp": 30, "input_length": 512, "output_length": 8, "hash_ids": [7]},
        ]
        parents, shared = assign_parents(records)
        self.assertEqual(parents, [None, 0, 1, None])
        self.assertEqual(shared, [0, 2, 3, 0])

    def test_committed_mooncake_profiles_match_the_whole_trace(self):
        repo = Path(__file__).resolve().parents[1]
        conversation = json.loads((repo / "site/data/traces/conversation/profile.json").read_text())
        toolagent = json.loads((repo / "site/data/traces/toolagent/profile.json").read_text())
        synthetic = json.loads((repo / "site/data/traces/synthetic/profile.json").read_text())

        self.assertEqual(conversation["request_count"], 12031)
        self.assertEqual(conversation["sessions"]["count"], 7386)
        self.assertEqual(conversation["sessions"]["continued"], 4645)
        self.assertEqual(conversation["sessions"]["branches"], 13)
        self.assertEqual(conversation["sessions"]["longest"], 43)
        self.assertEqual(conversation["peak_per_second"], 28)
        self.assertEqual(conversation["median_input"], 6909)
        self.assertEqual(conversation["median_output"], 350)
        self.assertEqual(conversation["unique_blocks"], 182790)
        self.assertAlmostEqual(conversation["blocks_reused"], 0.3664, places=3)
        self.assertEqual(len(conversation["input_histogram"]), 9)
        self.assertTrue((repo / "site/data/traces/conversation/sessions.jsonl.gz").is_file())

        self.assertEqual(toolagent["request_count"], 23608)
        self.assertEqual(toolagent["sessions"]["count"], 6964)
        self.assertEqual(toolagent["sessions"]["branches"], 410)
        self.assertEqual(synthetic["request_count"], 3993)
        self.assertEqual(synthetic["sessions"]["count"], 2321)
        self.assertEqual(synthetic["sessions"]["branches"], 76)

    def test_trace_cards_use_the_profile_and_link_to_pages(self):
        cards = {card["id"]: card for card in list_builtin_trace_cards()}
        conversation = cards["conversation"]
        self.assertEqual(conversation["stats"]["request_count"], 12031)
        self.assertEqual(conversation["links"]["trace"], "/traces/conversation")
        self.assertGreater(len(conversation["charts"]["rpm"]), 30)
        self.assertIn("Apache-2.0", conversation["source_line"])
        self.assertFalse(conversation["availability"]["reference"])

        stress = cards["wall-clock-win"]
        self.assertGreater(stress["stats"]["request_count"], 4000)
        self.assertTrue(stress["charts"]["rpm"])
        self.assertEqual(stress["provenance"]["license"], "Apache-2.0")

        rag = cards["ragpulse"]
        self.assertTrue(rag["availability"]["reference"])
        self.assertIsNone(rag["charts"])
        self.assertEqual(rag["provenance"]["license"], "MIT")
        self.assertNotIn("codex-swebenchpro", cards)

    def test_small_profile_has_nine_input_buckets(self):
        profile = build_profile(
            [
                {"timestamp": 0, "input_length": 100, "output_length": 4, "hash_ids": [1]},
                {"timestamp": 1000, "input_length": 5000, "output_length": 4, "hash_ids": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]},
            ],
            trace_id="tiny",
        )
        self.assertEqual(len(profile["input_histogram"]), 9)
        self.assertEqual(profile["request_count"], 2)


class TracePageRouteTests(unittest.TestCase):
    def test_unknown_trace_page_still_renders_the_shell(self):
        client = app.test_client()
        response = client.get("/traces/not-a-trace")
        try:
            self.assertEqual(response.status_code, 200)
            self.assertIn(b"trace_page.js", response.data)
        finally:
            response.close()

    def test_missing_jsonl_is_not_a_download(self):
        client = app.test_client()
        response = client.get("/traces/ragpulse/trace.jsonl")
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
