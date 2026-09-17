import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aiperf_runner import (
    aiperf_status,
    build_subset,
    default_url,
    huggingface_token_configured,
    inference_api_key,
    looks_like_tracerator_port_conflict,
    normalize_server_model_name,
    replay_error_hint,
    resolve_hf_tokenizer,
    run_profile_replay,
    should_send_inference_api_key,
    summarize_analysis,
    summarize_replay_errors,
    validate_generated_trace,
    write_trace_bundle,
)
from app import app, generate_trace_data


class AiperfRunnerTests(unittest.TestCase):
    def test_write_trace_bundle_and_subset(self):
        reqs = [{"timestamp": 0, "input_length": 1024, "output_length": 32, "hash_ids": [1, 2]}]
        manifest = {"n_requests": 1, "approx_cache_hit_ratio": 0.1}
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir)
            trace_path, manifest_path = write_trace_bundle(work_dir, reqs, manifest)
            self.assertTrue(trace_path.exists())
            self.assertTrue(manifest_path.exists())
            subset_path = work_dir / "subset.jsonl"
            count = build_subset(trace_path, 1, subset_path)
            self.assertEqual(count, 1)
            self.assertEqual(subset_path.read_text(encoding="utf-8").strip().count("{"), 1)

    def test_summarize_analysis_includes_manifest_delta(self):
        summary = summarize_analysis(
            {"total_requests": 10, "cache_hit_rate": 0.42, "avg_isl": 1000, "avg_osl": 50},
            {"approx_cache_hit_ratio": 0.40},
        )
        self.assertEqual(summary["hit_ratio_delta"], 0.02)

    def test_huggingface_token_configured(self):
        with patch.dict(os.environ, {"HF_TOKEN": "hf_test", "HUGGING_FACE_HUB_TOKEN": ""}, clear=False):
            self.assertTrue(huggingface_token_configured())
        with patch.dict(os.environ, {"HF_TOKEN": "", "HUGGING_FACE_HUB_TOKEN": ""}, clear=False):
            self.assertFalse(huggingface_token_configured())

    def test_inference_api_key_prefers_explicit_value(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "from-env"}, clear=False):
            self.assertEqual(inference_api_key("from-ui"), "from-ui")

    def test_should_not_forward_openai_key_when_auth_not_required(self):
        from aiperf_runner import resolve_replay_api_key, LOCAL_INFERENCE_API_KEY_PLACEHOLDER
        with patch.dict(os.environ, {
            "OPENAI_API_KEY": "sk-proj-fake",
            "TRACERATOR_INFERENCE_API_KEY": "",
            "OMLX_API_KEY": "",
            "LLM_API_KEY": "",
            "VLLM_API_KEY": "",
        }, clear=False):
            self.assertFalse(should_send_inference_api_key(
                auth_required=False, url="http://host.docker.internal:8000", engine="omlx"
            ))
            self.assertEqual(
                resolve_replay_api_key(
                    auth_required=True,
                    url="http://host.docker.internal:8000",
                    allow_local_placeholder=True,
                    engine="omlx",
                ),
                LOCAL_INFERENCE_API_KEY_PLACEHOLDER,
            )
            self.assertIsNone(
                resolve_replay_api_key(
                    auth_required=True,
                    url="http://host.docker.internal:8000",
                    allow_local_placeholder=False,
                    engine="omlx",
                )
            )
        with patch.dict(os.environ, {
            "OPENAI_API_KEY": "sk-proj-fake",
            "TRACERATOR_INFERENCE_API_KEY": "",
            "OMLX_API_KEY": "omlx-secret",
            "LLM_API_KEY": "",
        }, clear=False):
            self.assertEqual(
                resolve_replay_api_key(auth_required=False, url="http://host.docker.internal:8000", engine="omlx"),
                "omlx-secret",
            )
        with patch.dict(os.environ, {
            "OPENAI_API_KEY": "sk-proj-fake",
            "TRACERATOR_INFERENCE_API_KEY": "local-key",
            "OMLX_API_KEY": "",
            "VLLM_API_KEY": "",
        }, clear=False):
            self.assertEqual(
                resolve_replay_api_key(auth_required=False, url="http://host.docker.internal:8000", engine="vllm"),
                "local-key",
            )
        self.assertTrue(should_send_inference_api_key(
            explicit="local-ui-key", auth_required=False, url="http://host.docker.internal:8000", engine="omlx"
        ))

    def test_backend_defaults(self):
        from aiperf_runner import list_inference_backends, get_inference_backend
        ids = {b["id"] for b in list_inference_backends()}
        self.assertEqual(ids, {"omlx", "vllm", "sglang", "ollama", "openai"})
        self.assertEqual(get_inference_backend("sglang")["port"], "30000")
        self.assertEqual(get_inference_backend("omlx")["auth_env"][0], "OMLX_API_KEY")

    def test_aiperf_subprocess_env_scrubs_openai_key(self):
        from aiperf_runner import aiperf_subprocess_env
        with patch.dict(os.environ, {
            "OPENAI_API_KEY": "sk-proj-fake",
            "TRACERATOR_INFERENCE_API_KEY": "",
            "HF_TOKEN": "hf_x",
        }, clear=False):
            scrubbed = aiperf_subprocess_env(send_api_key=False)
            self.assertNotIn("OPENAI_API_KEY", scrubbed)
            self.assertEqual(scrubbed.get("HF_TOKEN"), "hf_x")
            sent = aiperf_subprocess_env(send_api_key=True, api_key="local-key")
            self.assertEqual(sent.get("OPENAI_API_KEY"), "local-key")

    def test_inference_api_key_ignores_openai_by_default(self):
        with patch.dict(os.environ, {
            "OPENAI_API_KEY": "sk-proj-fake",
            "TRACERATOR_INFERENCE_API_KEY": "",
        }, clear=False):
            self.assertIsNone(inference_api_key())
            self.assertEqual(
                inference_api_key(include_openai_env=True),
                "sk-proj-fake",
            )

    def test_summarize_replay_errors_reads_profile_jsonl(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            artifact_dir = Path(tmpdir)
            jsonl = artifact_dir / "profile_export.jsonl"
            jsonl.write_text(
                json.dumps({
                    "error": {
                        "code": 401,
                        "type": "Unauthorized",
                        "message": "{\"error\":{\"message\":\"Invalid API key\",\"type\":\"authentication_error\"}}",
                    }
                }) + "\n",
                encoding="utf-8",
            )
            errors = summarize_replay_errors(artifact_dir)
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["code"], 401)
        self.assertEqual(errors[0]["message"], "Invalid API key")

    @patch("aiperf_runner.running_in_container", return_value=True)
    @patch("aiperf_runner.find_aiperf_binary", return_value=None)
    def test_validate_without_aiperf_reports_error(self, _mock_find, _mock_container):
        reqs, manifest = generate_trace_data({"base": "conversation", "scale": 0.01, "seed": 7})
        result = validate_generated_trace(reqs, manifest, {"mode": "analyze"})
        self.assertEqual(result["analysis"]["status"], "error")
        self.assertIn("container", result["analysis"]["error"])

    def test_generate_uses_ui_source_param(self):
        from app import resolve_trace_source_id

        self.assertEqual(resolve_trace_source_id({"source": "cc-weka-subagents"}), "cc-weka-subagents")
        self.assertEqual(
            resolve_trace_source_id({"source": "cc-weka-subagents", "base": "conversation"}),
            "cc-weka-subagents",
        )
        reqs, manifest = generate_trace_data({"source": "conversation", "scale": 0.01, "seed": 7})
        self.assertGreater(len(reqs), 0)
        self.assertEqual(manifest["base_source"]["id"], "conversation")
        self.assertEqual(manifest["params"]["source"], "conversation")
        self.assertEqual(manifest["params"]["base"], "conversation")

    @patch("aiperf_runner.run_analyze_trace")
    @patch("aiperf_runner.find_aiperf_binary", return_value="/usr/bin/aiperf")
    @patch("aiperf_runner.get_aiperf_version", return_value="aiperf 1.2.3")
    def test_validate_runs_static_analysis(self, _version, _find, mock_analyze):
        reqs, manifest = generate_trace_data({"base": "conversation", "scale": 0.01, "seed": 7})
        mock_analyze.return_value = (
            {
                "total_requests": len(reqs),
                "cache_hit_rate": 0.2,
                "avg_isl": 5000,
                "avg_osl": 100,
            },
            ["aiperf", "analyze-trace"],
        )
        result = validate_generated_trace(reqs, manifest, {"mode": "analyze"})
        self.assertEqual(result["analysis"]["status"], "ok")
        self.assertEqual(result["analysis"]["summary"]["total_requests"], len(reqs))
        self.assertEqual(result["replay"]["status"], "skipped")

    @patch("aiperf_runner.run_analyze_trace")
    @patch("aiperf_runner.find_aiperf_binary", return_value="/usr/bin/aiperf")
    @patch("aiperf_runner.get_aiperf_version", return_value="aiperf 1.2.3")
    def test_validate_analyzes_mooncake_source_for_multi_turn_replay(self, _version, _find, mock_analyze):
        reqs, manifest = generate_trace_data({
            "base": "conversation",
            "scale": 0.01,
            "seed": 7,
            "aiperf_dataset_style": "multi_turn",
        })
        mock_analyze.return_value = (
            {
                "total_requests": len(reqs),
                "cache_hit_rate": 0.2,
                "avg_isl": 5000,
                "avg_osl": 100,
            },
            ["aiperf", "analyze-trace"],
        )
        result = validate_generated_trace(reqs, manifest, {
            "mode": "analyze",
            "aiperf_dataset_style": "multi_turn",
        })
        self.assertEqual(result["analysis"]["status"], "ok")
        self.assertEqual(result["analysis"]["dataset_style"], "mooncake_trace")
        self.assertEqual(result["analysis"]["replay_dataset_style"], "multi_turn")
        self.assertEqual(result["trace"]["analysis_dataset_style"], "mooncake_trace")
        self.assertEqual(result["trace"]["aiperf_dataset_style"], "multi_turn")

    def test_normalize_server_model_name(self):
        self.assertEqual(
            normalize_server_model_name("mlx-community--Qwen2.5-7B-Instruct-4bit"),
            "mlx-community/Qwen2.5-7B-Instruct-4bit",
        )

    def test_resolve_hf_tokenizer_maps_short_qwen_names(self):
        self.assertEqual(
            resolve_hf_tokenizer("Qwen2.5-7B-Instruct-4bit"),
            "Qwen/Qwen2.5-7B-Instruct",
        )
        self.assertEqual(
            resolve_hf_tokenizer("mlx-community/Qwen2.5-7B-Instruct-4bit"),
            "Qwen/Qwen2.5-7B-Instruct",
        )
        self.assertEqual(
            resolve_hf_tokenizer("Qwen/Qwen3-0.6B"),
            "Qwen/Qwen3-0.6B",
        )

    def test_replay_error_hint_for_tokenizer_not_found(self):
        hint = replay_error_hint([{
            "code": None,
            "type": "error",
            "message": "Failed to load tokenizer 'Qwen2.5-7B-Instruct-4bit': OSError: not a valid model identifier",
        }])
        self.assertIsNotNone(hint)
        self.assertIn("Qwen/Qwen2.5-7B-Instruct", hint)

    def test_status_reports_defaults(self):
        server = {
            "reachable": True,
            "default_model": "mlx-community/Qwen2.5-7B-Instruct-4bit",
            "auth_required": True,
        }
        with patch("aiperf_runner.find_aiperf_binary", return_value="/usr/bin/aiperf"), patch(
            "aiperf_runner.get_aiperf_version", return_value="aiperf 1.2.3"
        ), patch("aiperf_runner.probe_inference_server", return_value=server), patch(
            "aiperf_runner.running_in_container", return_value=True
        ), patch.dict("os.environ", {"TRACERATOR_INFERENCE_HOST": "host.docker.internal"}, clear=False):
            status = aiperf_status(engine="vllm")
        self.assertTrue(status["available"])
        self.assertEqual(status["defaults"]["model"], "mlx-community/Qwen2.5-7B-Instruct-4bit")
        self.assertTrue(status["server"]["auth_required"])

    def test_default_vllm_url_uses_conventional_port_8000(self):
        with patch.dict(os.environ, {"TRACERATOR_INFERENCE_HOST": "host.docker.internal"}, clear=False):
            os.environ.pop("TRACERATOR_INFERENCE_PORT", None)
            self.assertEqual(default_url("vllm"), "http://host.docker.internal:8000")
            self.assertEqual(default_url("omlx"), "http://host.docker.internal:8000")
            self.assertEqual(default_url("sglang"), "http://host.docker.internal:30000")
            self.assertEqual(default_url("ollama"), "http://host.docker.internal:11434")
        with patch.dict(os.environ, {
            "TRACERATOR_INFERENCE_HOST": "host.docker.internal",
            "TRACERATOR_INFERENCE_PORT": "9000",
        }, clear=False):
            self.assertEqual(default_url("vllm"), "http://host.docker.internal:9000")

    def test_detects_tracerator_port_conflict(self):
        self.assertTrue(looks_like_tracerator_port_conflict("http://host.docker.internal:8008"))
        self.assertTrue(looks_like_tracerator_port_conflict("http://localhost:8008"))
        self.assertFalse(looks_like_tracerator_port_conflict("http://host.docker.internal:8000"))
        self.assertFalse(looks_like_tracerator_port_conflict("http://192.168.1.10:8008"))

    def test_replay_error_hint_for_flask_405(self):
        hint = replay_error_hint([{
            "code": 405,
            "type": "METHOD NOT ALLOWED",
            "message": "<!doctype html> <title>405 Method Not Allowed</title>",
        }])
        self.assertIsNotNone(hint)
        self.assertIn("8008", hint)
        self.assertIn("405", hint)

    @patch("aiperf_runner.subprocess.run")
    def test_run_profile_replay_uses_custom_dataset_type(self, mock_run):
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = ""
        mock_run.return_value.stderr = ""
        with tempfile.TemporaryDirectory() as tmpdir:
            trace_path = Path(tmpdir) / "single.jsonl"
            trace_path.write_text('{"text":"hello","output_length":8}\n', encoding="utf-8")
            _code, command, _detail = run_profile_replay(
                "/usr/bin/aiperf",
                trace_path,
                Path(tmpdir) / "artifacts",
                engine="vllm",
                model="Qwen/Qwen3-0.6B",
                tokenizer="Qwen/Qwen3-0.6B",
                url="http://localhost:8000",
                fixed_schedule=True,
                custom_dataset_type="single_turn",
                request_count=5,
                use_server_token_count=True,
            )
        self.assertIn("single_turn", command)
        self.assertIn("--request-count", command)
        self.assertIn("5", command)
        self.assertNotIn("--use-server-token-count", command)

    @patch("aiperf_runner.subprocess.run")
    def test_run_profile_replay_uses_num_conversations_for_random_pool(self, mock_run):
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = ""
        mock_run.return_value.stderr = ""
        with tempfile.TemporaryDirectory() as tmpdir:
            trace_path = Path(tmpdir) / "pool.jsonl"
            trace_path.write_text('{"text":"hello","output_length":8}\n', encoding="utf-8")
            _code, command, _detail = run_profile_replay(
                "/usr/bin/aiperf",
                trace_path,
                Path(tmpdir) / "artifacts",
                engine="vllm",
                model="Qwen/Qwen3-0.6B",
                tokenizer="Qwen/Qwen3-0.6B",
                url="http://localhost:8000",
                fixed_schedule=True,
                custom_dataset_type="random_pool",
                request_count=9,
            )
        self.assertIn("random_pool", command)
        self.assertIn("--num-conversations", command)
        self.assertIn("9", command)
        self.assertIn("--no-fixed-schedule", command)
        self.assertNotIn("--fixed-schedule", command)

    @patch("aiperf_runner.subprocess.run")
    def test_run_profile_replay_uses_public_dataset(self, mock_run):
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = ""
        mock_run.return_value.stderr = ""
        with tempfile.TemporaryDirectory() as tmpdir:
            trace_path = Path(tmpdir) / "trace.jsonl"
            trace_path.write_text('{"timestamp":0,"input_length":8,"output_length":8}\n', encoding="utf-8")
            _code, command, _detail = run_profile_replay(
                "/usr/bin/aiperf",
                trace_path,
                Path(tmpdir) / "artifacts",
                engine="vllm",
                model="Qwen/Qwen2-VL-2B-Instruct",
                tokenizer="Qwen/Qwen2-VL-2B-Instruct",
                url="http://localhost:8000",
                fixed_schedule=True,
                public_dataset="vision_arena",
                hf_subset="train",
                endpoint_type="chat",
                request_count=10,
            )
        self.assertIn("--public-dataset", command)
        self.assertIn("vision_arena", command)
        self.assertIn("--hf-subset", command)
        self.assertIn("train", command)
        self.assertIn("--request-count", command)
        self.assertIn("10", command)
        self.assertIn("--no-fixed-schedule", command)
        self.assertNotIn("--input-file", command)
        self.assertNotIn("--custom-dataset-type", command)
        self.assertNotIn("--use-server-token-count", command)


class AiperfApiTests(unittest.TestCase):
    def test_status_route(self):
        client = app.test_client()
        with patch("app.aiperf_status", return_value={"available": True, "version": "aiperf 1.2.3"}):
            resp = client.get("/aiperf/status?engine=vllm")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json()["available"])

    def test_dataset_styles_route(self):
        client = app.test_client()
        resp = client.get("/aiperf/dataset-styles")
        self.assertEqual(resp.status_code, 200)
        ids = {style["id"] for style in resp.get_json()["styles"]}
        self.assertEqual(ids, {"mooncake_trace", "single_turn", "multi_turn", "random_pool"})

    def test_public_datasets_route(self):
        client = app.test_client()
        resp = client.get("/aiperf/public-datasets")
        self.assertEqual(resp.status_code, 200)
        ids = {dataset["id"] for dataset in resp.get_json()["datasets"]}
        self.assertIn("vision_arena", ids)
        self.assertIn("speed_bench_throughput_32k", ids)

    def test_validate_route_returns_manifest_and_analysis(self):
        client = app.test_client()
        fake_result = {
            "aiperf": {"available": True, "binary": "/usr/bin/aiperf", "version": "aiperf 1.2.3"},
            "trace": {"n_requests": 3, "subset_used": 3},
            "manifest": {"n_requests": 3},
            "analysis": {"status": "ok", "summary": {"total_requests": 3}},
            "replay": {"status": "skipped"},
        }
        with patch("app.validate_generated_trace", return_value=fake_result):
            resp = client.post(
                "/aiperf/validate",
                json={"trace_params": {"base": "conversation", "scale": 0.01}, "mode": "analyze"},
            )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["analysis"]["status"], "ok")
        self.assertEqual(data["manifest"]["n_requests"], 3)

    def test_validate_route_rejects_unavailable_source(self):
        client = app.test_client()
        resp = client.post(
            "/aiperf/validate",
            json={"trace_params": {"base": "burstgpt", "scale": 1.0}, "mode": "analyze"},
        )
        self.assertEqual(resp.status_code, 400)


if __name__ == "__main__":
    unittest.main()
