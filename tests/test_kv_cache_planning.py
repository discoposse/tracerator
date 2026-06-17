import unittest

from kv_cache_planning import BYTES_PER_GIB, cache_bytes_per_token, plan_kv_cache


class KVCachePlanningTests(unittest.TestCase):
    def test_standard_gqa_bytes_per_token(self):
        profile = cache_bytes_per_token("qwen3-32b-gqa", precision="bf16_fp16")

        self.assertEqual(profile["formula"], "standard_gqa")
        self.assertEqual(profile["elements_per_token"], 64 * 2 * 8 * 128)
        self.assertEqual(profile["bytes_per_token"], 64 * 2 * 8 * 128 * 2)

    def test_dsa_indexer_precision_is_separate(self):
        profile = cache_bytes_per_token(
            "deepseek-v3.2",
            precision="fp8_int8",
            indexer_precision="fp4_int4",
            include_draft_kv=True,
        )

        expected_kv = (61 + 1) * (512 + 64) * 1
        expected_indexer = (61 + 1) * 128 * 0.5
        self.assertEqual(profile["kv_bytes_per_token"], expected_kv)
        self.assertEqual(profile["indexer_bytes_per_token"], expected_indexer)
        self.assertEqual(profile["bytes_per_token"], expected_kv + expected_indexer)

    def test_optimal_policy_can_bypass_low_value_block(self):
        reqs = [
            {"timestamp": idx, "input_length": 1, "output_length": 1, "hash_ids": [block]}
            for idx, block in enumerate([1, 2, 3, 1, 2])
        ]
        bytes_per_block = cache_bytes_per_token("kimi-k2.5", precision="bf16_fp16")["bytes_per_token"]
        two_blocks_gib = ((bytes_per_block * 2) / BYTES_PER_GIB) + 1e-9

        planning = plan_kv_cache(
            reqs,
            model_id="kimi-k2.5",
            precision="bf16_fp16",
            block_size=1,
            capacity_gib_values=[two_blocks_gib],
            warmup_fraction=0,
        )
        result = planning["points"][0]["results"]

        self.assertEqual(planning["points"][0]["cache_blocks"], 2)
        self.assertEqual(result["fifo"]["hit_rate"], 0)
        self.assertEqual(result["lru"]["hit_rate"], 0)
        self.assertEqual(result["optimal"]["hit_rate"], 0.4)


if __name__ == "__main__":
    unittest.main()
