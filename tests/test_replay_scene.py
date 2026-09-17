"""Tests for bounded trace replay scene documents."""

from __future__ import annotations

import unittest

from replay_scene import build_replay_scene


def _records(n: int = 100):
    records = []
    for index in range(n):
        root = [1, 2, 3] if index % 3 else [7, 8, 9]
        records.append({
            "timestamp": index * 50,
            "input_length": 2560,
            "output_length": 20 + index % 5,
            "hash_ids": root + [1000 + index, 2000 + index],
        })
    return records


class ReplaySceneTests(unittest.TestCase):
    def test_scene_is_bounded_and_has_both_views(self):
        scene = build_replay_scene(_records(), max_requests=40, max_frames=30)
        self.assertEqual(scene["schema_version"], "tracerator.replay_scene.v1")
        self.assertLessEqual(len(scene["requests"]), 40)
        self.assertLessEqual(len(scene["frames"]), 30)
        self.assertEqual(len(scene["views"]["physical"]["nodes"]), 6)
        self.assertGreater(len(scene["views"]["logical"]["nodes"]), 1)
        self.assertTrue(all("prefix_id" in row for row in scene["requests"]))

    def test_sizing_and_replay_metrics_are_carried_into_scene(self):
        sizing = {
            "inputs": {"cache_gib_per_replica": 20},
            "cache": {"working_set_gib": 10},
            "recommendation": {
                "replicas": 2,
                "gpus": 2,
                "capacity_slots": 32,
                "ttft_ms": {"p95": 450},
            },
            "recommended_request_samples": [
                {
                    "index": 0,
                    "arrival_ms": 0,
                    "start_ms": 12,
                    "completion_ms": 300,
                    "queue_ms": 12,
                    "replica": 1,
                }
            ],
        }
        replay = {
            "latency_ms": {
                "time_to_first_token": {"p95": 500},
                "request_latency": {"p95": 900},
            }
        }
        scene = build_replay_scene(
            _records(),
            sizing_result=sizing,
            replay_result=replay,
        )
        self.assertTrue(scene["source"]["has_sizing"])
        self.assertTrue(scene["source"]["has_replay"])
        self.assertEqual(scene["metrics"]["sizing"]["replicas"], 2)
        self.assertEqual(scene["metrics"]["observed"]["ttft_p95_ms"], 500)
        self.assertEqual(scene["requests"][0]["queue_ms"], 12)

    def test_empty_trace_rejected(self):
        with self.assertRaises(ValueError):
            build_replay_scene([])


if __name__ == "__main__":
    unittest.main()
