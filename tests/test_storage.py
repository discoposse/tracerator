"""Tests for SQLite run workspace storage."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import storage


class StorageTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db = Path(self._tmpdir.name) / "test.db"
        self.runs_root = Path(self._tmpdir.name) / "runs"
        storage.init_db(self.db)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_create_list_get_delete(self):
        run = storage.create_run(
            name="Demo",
            source="conversation",
            scenario="standard",
            params={"scale": 0.05},
            db_path=self.db,
            runs_root=self.runs_root,
        )
        self.assertEqual(run["status"], "draft")
        self.assertTrue(run["id"])
        self.assertTrue((self.runs_root / run["id"]).is_dir())

        listed = storage.list_runs(db_path=self.db)
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["name"], "Demo")

        fetched = storage.get_run(run["id"], db_path=self.db, full=True)
        self.assertEqual(fetched["params"]["scale"], 0.05)

        self.assertTrue(storage.delete_run(run["id"], db_path=self.db, runs_root=self.runs_root))
        self.assertIsNone(storage.get_run(run["id"], db_path=self.db))
        self.assertFalse((self.runs_root / run["id"]).exists())

    def test_write_trace_and_update(self):
        run = storage.create_run(name="T", db_path=self.db, runs_root=self.runs_root)
        records = [
            {"timestamp": 0, "input_length": 512, "output_length": 10, "hash_ids": [1]},
            {"timestamp": 100, "input_length": 1024, "output_length": 20, "hash_ids": [1, 2]},
        ]
        path = storage.write_trace_jsonl(run["id"], records, runs_root=self.runs_root)
        self.assertTrue(path.exists())
        loaded = storage.load_trace_records(run["id"], runs_root=self.runs_root)
        self.assertEqual(len(loaded), 2)

        updated = storage.update_run(
            run["id"],
            status="generated",
            request_count=2,
            integrity_ok=True,
            manifest={"n_requests": 2},
            narrative={"summary": "hello", "markdown": "# hello"},
            db_path=self.db,
        )
        self.assertEqual(updated["status"], "generated")
        self.assertEqual(updated["request_count"], 2)
        self.assertTrue(updated["integrity_ok"])
        self.assertEqual(updated["manifest"]["n_requests"], 2)

    def test_invalid_status_rejected(self):
        run = storage.create_run(name="X", db_path=self.db, runs_root=self.runs_root)
        with self.assertRaises(ValueError):
            storage.update_run(run["id"], status="bogus", db_path=self.db)


if __name__ == "__main__":
    unittest.main()
