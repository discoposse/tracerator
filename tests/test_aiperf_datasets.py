import unittest

from aiperf_datasets import (
    convert_mooncake_records,
    dataset_style_manifest,
    list_aiperf_dataset_styles,
    list_aiperf_public_datasets,
    normalize_aiperf_public_dataset,
    normalize_aiperf_dataset_style,
    public_dataset_manifest,
)
from app import generate_trace_data


class AiperfDatasetTests(unittest.TestCase):
    def test_catalog_contains_all_custom_dataset_styles(self):
        ids = {style["id"] for style in list_aiperf_dataset_styles()}
        self.assertEqual(ids, {"mooncake_trace", "single_turn", "multi_turn", "random_pool"})
        for style in list_aiperf_dataset_styles():
            self.assertTrue(style["description"])
            self.assertTrue(style["best_for"])
            self.assertTrue(style["version"])

    def test_normalizes_aliases(self):
        self.assertEqual(normalize_aiperf_dataset_style("single"), "single_turn")
        self.assertEqual(normalize_aiperf_dataset_style("pool"), "random_pool")
        self.assertEqual(normalize_aiperf_dataset_style("unknown"), "mooncake_trace")

    def test_public_dataset_catalog_includes_official_loaders(self):
        datasets = {dataset["id"]: dataset for dataset in list_aiperf_public_datasets()}
        for dataset_id in [
            "sharegpt",
            "instruct_coder",
            "aimo",
            "mmstar",
            "mmvu",
            "llava_onevision",
            "vision_arena",
            "blazedit_5k",
            "blazedit_10k",
            "spec_bench",
            "speed_bench_qualitative",
            "speed_bench_throughput_32k_high_entropy",
            "librispeech",
            "spgispeech",
        ]:
            self.assertIn(dataset_id, datasets)
            self.assertTrue(datasets[dataset_id]["description"])
            self.assertTrue(datasets[dataset_id]["best_for"])
        self.assertTrue(datasets["vision_arena"]["requires_hf_token"])
        self.assertEqual(datasets["vision_arena"]["modality"], "image")

    def test_normalizes_public_dataset_aliases(self):
        self.assertEqual(normalize_aiperf_public_dataset("visionarena"), "vision_arena")
        self.assertEqual(normalize_aiperf_public_dataset("speedbench"), "speed_bench_qualitative")
        self.assertEqual(normalize_aiperf_public_dataset("unknown"), None)
        self.assertEqual(public_dataset_manifest("llava")["id"], "llava_onevision")

    def test_single_turn_conversion_includes_doc_fields(self):
        records = [{"timestamp": 7, "input_length": 1024, "output_length": 64, "hash_ids": [1, 2]}]
        converted = convert_mooncake_records(records, "single_turn")
        self.assertEqual(len(converted), 1)
        self.assertIn("text", converted[0])
        self.assertEqual(converted[0]["output_length"], 64)
        self.assertEqual(converted[0]["extra"]["tracerator_target_input_length"], 1024)

    def test_multi_turn_conversion_groups_sessions(self):
        records = [
            {"trace_id": "a", "timestamp": 100, "input_length": 100, "output_length": 10, "hash_ids": [1]},
            {"trace_id": "a", "timestamp": 250, "input_length": 120, "output_length": 11, "hash_ids": [1]},
            {"trace_id": "b", "timestamp": 300, "input_length": 130, "output_length": 12, "hash_ids": [2]},
        ]
        converted = convert_mooncake_records(records, "multi_turn")
        self.assertEqual(len(converted), 2)
        self.assertEqual(converted[0]["session_id"], "a")
        self.assertEqual(converted[0]["timestamp"], 100)
        self.assertEqual(len(converted[0]["turns"]), 2)
        self.assertEqual(converted[0]["turns"][0]["timestamp"], 100)
        self.assertEqual(converted[0]["turns"][1]["delay"], 150)

    def test_generated_manifest_records_selected_aiperf_dataset(self):
        _reqs, manifest = generate_trace_data({
            "source": "conversation",
            "scale": 0.01,
            "seed": 7,
            "aiperf_dataset_style": "random_pool",
        })
        self.assertEqual(manifest["aiperf_dataset"], dataset_style_manifest("random_pool"))
        self.assertEqual(manifest["params"]["aiperf_dataset_style"], "random_pool")


if __name__ == "__main__":
    unittest.main()
