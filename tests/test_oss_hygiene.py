"""Public-tree checks for licenses and vendor-neutral wording."""

import unittest
from pathlib import Path

from trace_sources import discover_trace_sources

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", "artifacts", "__pycache__", ".venv"}
TEXT_SUFFIXES = {".py", ".md", ".html", ".js", ".css", ".yml", ".yaml", ".sh", ".txt", ".example"}


def _text_files():
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES and path.name not in {".env.example"}:
            continue
        yield path


class OpenSourceHygieneTests(unittest.TestCase):
    def test_every_registry_trace_has_license_source_and_citation(self):
        for source in discover_trace_sources():
            self.assertTrue(source.license, source.id)
            self.assertTrue(source.source_url, source.id)
            self.assertTrue(source.citation, source.id)
            self.assertTrue(source.citation_url, source.id)
            self.assertTrue(source.source_url.startswith("https://"), source.id)

    def test_removed_source_and_paper_are_gone(self):
        ids = {source.id for source in discover_trace_sources()}
        self.assertNotIn("codex-swebenchpro", ids)
        self.assertFalse((ROOT / "Mooncake" / "Mooncake-FAST25.pdf").exists())
        self.assertFalse((ROOT / "docs" / ("LIGHTINFERRA_" + "D" + "DN_GOLDEN_TRACE.md")).exists())
        self.assertTrue((ROOT / "docs" / "turn-1-warm-turn-2-resume.md").exists())
        notices = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
        self.assertIn("Apache-2.0", notices)
        self.assertIn("arxiv.org/abs/2407.00079", notices)
        self.assertIn("ai-dynamo/aiperf", notices)

    def test_public_tree_does_not_name_internal_products(self):
        forbidden = ("inf" + "inia", "d" + "dn")
        for path in _text_files():
            try:
                text = path.read_text(encoding="utf-8").lower()
            except UnicodeError:
                continue
            for word in forbidden:
                self.assertNotIn(word, text, f"{word} in {path.relative_to(ROOT)}")


if __name__ == "__main__":
    unittest.main()
