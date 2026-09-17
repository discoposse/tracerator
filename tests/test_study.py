import unittest
from unittest.mock import patch

from study import (
    PROVIDERS,
    build_run_study_slice,
    build_study_context,
    list_providers,
    summarize_study,
)


class StudyModuleTests(unittest.TestCase):
    def test_providers_include_openai_and_xai(self):
        ids = {p["id"] for p in list_providers()}
        self.assertEqual(ids, {"openai", "xai"})
        self.assertIn("default_model", PROVIDERS["xai"])
        self.assertEqual(PROVIDERS["xai"]["default_model"], "grok-4.5")
        self.assertIn("grok-4.3", PROVIDERS["xai"]["models"])
        self.assertNotIn("grok-2-latest", PROVIDERS["xai"]["models"])

    def test_build_run_study_slice(self):
        records = [
            {"timestamp": 0, "input_length": 1024, "output_length": 32, "hash_ids": [1, 2]},
            {"timestamp": 10, "input_length": 2048, "output_length": 40, "hash_ids": [1, 2, 3, 4]},
            {"timestamp": 20, "input_length": 1024, "output_length": 16, "hash_ids": [1, 2]},
        ]
        run = {
            "id": "abc",
            "name": "demo",
            "status": "generated",
            "params": {"source": "conversation", "scenario": "standard"},
            "manifest": {
                "n_requests": 3,
                "base_source": {"label": "Conversation"},
                "trace_scenario": {"label": "standard"},
                "approx_cache_hit_ratio": 0.2,
            },
            "replay_result": None,
        }
        slice_ = build_run_study_slice(run=run, records=records)
        self.assertEqual(slice_["id"], "abc")
        self.assertEqual(slice_["name"], "demo")
        self.assertEqual(slice_["source"], "Conversation")
        self.assertEqual(slice_["generated_from"]["label"], "Conversation")
        self.assertFalse(slice_["source_mismatch"])
        self.assertEqual(slice_["overview"]["request_count"], 3)
        self.assertIn("prefix", slice_)
        self.assertIn("viz", slice_)
        self.assertNotIn("markdown", slice_["narrative"])
        ctx = build_study_context([slice_])
        self.assertEqual(ctx["run_count"], 1)
        self.assertEqual(len(ctx["runs"]), 1)

    def test_source_mismatch_flagged_when_requested_differs(self):
        records = [
            {"timestamp": 0, "input_length": 512, "output_length": 8, "hash_ids": [1]},
        ]
        run = {
            "id": "weka-named",
            "name": "WEKA",
            "status": "generated",
            "params": {"source": "cc-weka-subagents", "scenario": "standard"},
            "manifest": {
                "n_requests": 1,
                "base_source": {"id": "conversation", "label": "Mooncake Conversation"},
            },
        }
        slice_ = build_run_study_slice(run=run, records=records)
        self.assertTrue(slice_["source_mismatch"])
        self.assertEqual(slice_["requested_source"], "cc-weka-subagents")
        self.assertEqual(slice_["generated_from"]["id"], "conversation")
        self.assertEqual(slice_["source"], "Mooncake Conversation")

    def test_summarize_requires_key(self):
        with self.assertRaises(ValueError):
            summarize_study({"runs": []}, provider="openai", api_key="")

    @patch("study._chat_completions")
    def test_summary_prompt_enforces_report_presentation_contract(self, mock_chat):
        mock_chat.return_value = {
            "provider": "openai",
            "model": "test-model",
            "content": "## Highlight\n\nMeasured summary.",
            "usage": None,
        }
        result = summarize_study(
            {"runs": [{"name": "GGM"}]},
            provider="openai",
            api_key="session-key",
            model="test-model",
        )
        self.assertEqual(result["format"], "markdown")
        prompt = mock_chat.call_args.kwargs["messages"][1]["content"]
        self.assertIn("one Markdown table with columns Run, Baseline, Status", prompt)
        self.assertIn("Start each with a short bold takeaway label", prompt)
        self.assertIn("Do not use code fences or HTML", prompt)

    def test_study_ui_has_safe_table_and_report_renderers(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        javascript = (root / "site" / "js" / "study.js").read_text(encoding="utf-8")
        html = (root / "site" / "study.html").read_text(encoding="utf-8")
        self.assertIn("function renderMarkdownTable(lines)", javascript)
        self.assertIn('class="study-report-table"', javascript)
        self.assertIn("function renderStudyReport(md, meta)", javascript)
        self.assertIn("inlineMarkdown", javascript)
        self.assertIn('id="summary-out" class="study-markdown"', html)


if __name__ == "__main__":
    unittest.main()
