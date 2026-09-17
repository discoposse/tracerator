"""SQLite persistence for Tracerator workflow runs (WAL mode).

Large traces live on disk under artifacts/runs/{id}/; index columns and JSON
blobs (manifest, analysis, replay, narrative) live in SQLite so runs reopen
without re-uploading files.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id                   TEXT PRIMARY KEY,
    name                 TEXT NOT NULL,
    source               TEXT,
    scenario             TEXT,
    status               TEXT NOT NULL DEFAULT 'draft',
    created_at           INTEGER NOT NULL,
    updated_at           INTEGER NOT NULL,
    request_count        INTEGER,
    max_concurrency      REAL,
    approx_cache_hit     REAL,
    integrity_ok         INTEGER,
    has_analysis         INTEGER NOT NULL DEFAULT 0,
    has_replay           INTEGER NOT NULL DEFAULT 0,
    params_json          TEXT,
    manifest_json        TEXT,
    analysis_json        TEXT,
    replay_result_json   TEXT,
    narrative_json       TEXT,
    notes                TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_created ON runs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status);
CREATE INDEX IF NOT EXISTS idx_runs_source ON runs(source);

CREATE VIRTUAL TABLE IF NOT EXISTS runs_fts USING fts5(
    id UNINDEXED,
    name,
    narrative_text,
    tokenize = 'porter'
);
"""

_INDEX_COLS = (
    "id, name, source, scenario, status, created_at, updated_at, "
    "request_count, max_concurrency, approx_cache_hit, integrity_ok, "
    "has_analysis, has_replay, notes"
)

VALID_STATUSES = frozenset(
    {"draft", "generated", "validated", "replayed", "exported"}
)


def repo_root() -> Path:
    return Path(__file__).resolve().parent


def default_db_path() -> Path:
    env = (os.environ.get("TRACERATOR_DB") or "").strip()
    if env:
        return Path(env).expanduser()
    return repo_root() / "artifacts" / "tracerator.db"


def default_runs_root() -> Path:
    env = (os.environ.get("TRACERATOR_RUNS_ROOT") or "").strip()
    if env:
        return Path(env).expanduser()
    return repo_root() / "artifacts" / "runs"


def _now_ms() -> int:
    return int(time.time() * 1000)


def _connect(db_path: Optional[Path] = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


class _DbConn:
    """Context manager that always closes the SQLite connection."""

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None

    def __enter__(self) -> sqlite3.Connection:
        self._conn = _connect(self._db_path)
        return self._conn

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None


def init_db(db_path: Optional[Path] = None) -> Path:
    path = Path(db_path) if db_path else default_db_path()
    with _DbConn(path) as conn:
        conn.executescript(SCHEMA)
        conn.commit()
    return path


def run_dir(run_id: str, runs_root: Optional[Path] = None) -> Path:
    root = Path(runs_root) if runs_root else default_runs_root()
    path = root / run_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def trace_path(run_id: str, runs_root: Optional[Path] = None) -> Path:
    return run_dir(run_id, runs_root) / "trace.jsonl"


def _dumps(value: Any) -> Optional[str]:
    if value is None:
        return None
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def _loads(text: Optional[str]) -> Any:
    if not text:
        return None
    return json.loads(text)


def _row_to_index(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "source": row["source"],
        "scenario": row["scenario"],
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "request_count": row["request_count"],
        "max_concurrency": row["max_concurrency"],
        "approx_cache_hit": row["approx_cache_hit"],
        "integrity_ok": bool(row["integrity_ok"]) if row["integrity_ok"] is not None else None,
        "has_analysis": bool(row["has_analysis"]),
        "has_replay": bool(row["has_replay"]),
        "notes": row["notes"],
    }


def _row_to_full(row: sqlite3.Row) -> Dict[str, Any]:
    out = _row_to_index(row)
    out["params"] = _loads(row["params_json"])
    out["manifest"] = _loads(row["manifest_json"])
    out["analysis"] = _loads(row["analysis_json"])
    out["replay_result"] = _loads(row["replay_result_json"])
    out["narrative"] = _loads(row["narrative_json"])
    out["trace_path"] = str(trace_path(row["id"])) if trace_path(row["id"]).exists() else None
    return out


def create_run(
    *,
    name: Optional[str] = None,
    source: Optional[str] = None,
    scenario: Optional[str] = None,
    params: Optional[Dict[str, Any]] = None,
    notes: Optional[str] = None,
    db_path: Optional[Path] = None,
    runs_root: Optional[Path] = None,
) -> Dict[str, Any]:
    init_db(db_path)
    run_id = uuid.uuid4().hex[:12]
    now = _now_ms()
    display = (name or "").strip() or f"Run {run_id}"
    run_dir(run_id, runs_root)
    with _DbConn(db_path) as conn:
        conn.execute(
            """
            INSERT INTO runs (
                id, name, source, scenario, status, created_at, updated_at,
                params_json, notes
            ) VALUES (?, ?, ?, ?, 'draft', ?, ?, ?, ?)
            """,
            (
                run_id,
                display,
                source,
                scenario or "standard",
                now,
                now,
                _dumps(params or {}),
                notes,
            ),
        )
        conn.execute(
            "INSERT INTO runs_fts(id, name, narrative_text) VALUES (?, ?, ?)",
            (run_id, display, ""),
        )
        conn.commit()
    return get_run(run_id, db_path=db_path, full=True)  # type: ignore[return-value]


def list_runs(
    *,
    limit: int = 100,
    offset: int = 0,
    status: Optional[str] = None,
    query: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    init_db(db_path)
    limit = max(1, min(500, int(limit)))
    offset = max(0, int(offset))
    with _DbConn(db_path) as conn:
        if query and query.strip():
            sql = f"""
                SELECT {_INDEX_COLS}
                FROM runs
                WHERE id IN (
                    SELECT id FROM runs_fts WHERE runs_fts MATCH ?
                )
            """
            args: List[Any] = [query.strip()]
            if status:
                sql += " AND status = ?"
                args.append(status)
            sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
            args.extend([limit, offset])
            rows = conn.execute(sql, args).fetchall()
        else:
            sql = f"SELECT {_INDEX_COLS} FROM runs"
            args = []
            if status:
                sql += " WHERE status = ?"
                args.append(status)
            sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
            args.extend([limit, offset])
            rows = conn.execute(sql, args).fetchall()
    return [_row_to_index(row) for row in rows]


def get_run(
    run_id: str,
    *,
    db_path: Optional[Path] = None,
    full: bool = True,
) -> Optional[Dict[str, Any]]:
    init_db(db_path)
    with _DbConn(db_path) as conn:
        if full:
            row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
            return _row_to_full(row) if row else None
        row = conn.execute(
            f"SELECT {_INDEX_COLS} FROM runs WHERE id = ?", (run_id,)
        ).fetchone()
        return _row_to_index(row) if row else None


def delete_run(
    run_id: str,
    *,
    db_path: Optional[Path] = None,
    runs_root: Optional[Path] = None,
) -> bool:
    init_db(db_path)
    with _DbConn(db_path) as conn:
        cur = conn.execute("DELETE FROM runs WHERE id = ?", (run_id,))
        conn.execute("DELETE FROM runs_fts WHERE id = ?", (run_id,))
        conn.commit()
        deleted = cur.rowcount > 0
    disk = (Path(runs_root) if runs_root else default_runs_root()) / run_id
    if disk.exists():
        shutil.rmtree(disk, ignore_errors=True)
    return deleted


def update_run(
    run_id: str,
    *,
    name: Optional[str] = None,
    status: Optional[str] = None,
    source: Optional[str] = None,
    scenario: Optional[str] = None,
    params: Optional[Dict[str, Any]] = None,
    manifest: Optional[Dict[str, Any]] = None,
    analysis: Optional[Dict[str, Any]] = None,
    replay_result: Optional[Dict[str, Any]] = None,
    narrative: Optional[Dict[str, Any]] = None,
    notes: Optional[str] = None,
    request_count: Optional[int] = None,
    max_concurrency: Optional[float] = None,
    approx_cache_hit: Optional[float] = None,
    integrity_ok: Optional[bool] = None,
    has_analysis: Optional[bool] = None,
    has_replay: Optional[bool] = None,
    db_path: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    init_db(db_path)
    if status is not None and status not in VALID_STATUSES:
        raise ValueError(f"invalid status: {status}")

    fields: Dict[str, Any] = {"updated_at": _now_ms()}
    if name is not None:
        fields["name"] = name
    if status is not None:
        fields["status"] = status
    if source is not None:
        fields["source"] = source
    if scenario is not None:
        fields["scenario"] = scenario
    if params is not None:
        fields["params_json"] = _dumps(params)
    if manifest is not None:
        fields["manifest_json"] = _dumps(manifest)
    if analysis is not None:
        fields["analysis_json"] = _dumps(analysis)
        fields["has_analysis"] = 1
    if replay_result is not None:
        fields["replay_result_json"] = _dumps(replay_result)
        fields["has_replay"] = 1
    if narrative is not None:
        fields["narrative_json"] = _dumps(narrative)
    if notes is not None:
        fields["notes"] = notes
    if request_count is not None:
        fields["request_count"] = request_count
    if max_concurrency is not None:
        fields["max_concurrency"] = max_concurrency
    if approx_cache_hit is not None:
        fields["approx_cache_hit"] = approx_cache_hit
    if integrity_ok is not None:
        fields["integrity_ok"] = 1 if integrity_ok else 0
    if has_analysis is not None:
        fields["has_analysis"] = 1 if has_analysis else 0
    if has_replay is not None:
        fields["has_replay"] = 1 if has_replay else 0

    if len(fields) == 1:
        return get_run(run_id, db_path=db_path, full=True)

    assignments = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [run_id]
    with _DbConn(db_path) as conn:
        cur = conn.execute(f"UPDATE runs SET {assignments} WHERE id = ?", values)
        if cur.rowcount == 0:
            return None
        if name is not None or narrative is not None:
            narr_text = ""
            if narrative is not None:
                narr_text = str(narrative.get("markdown") or narrative.get("summary") or "")
            elif name is not None:
                existing = conn.execute(
                    "SELECT narrative_json FROM runs WHERE id = ?", (run_id,)
                ).fetchone()
                if existing and existing["narrative_json"]:
                    loaded = _loads(existing["narrative_json"]) or {}
                    narr_text = str(loaded.get("markdown") or loaded.get("summary") or "")
            display = name
            if display is None:
                row = conn.execute("SELECT name FROM runs WHERE id = ?", (run_id,)).fetchone()
                display = row["name"] if row else run_id
            conn.execute("DELETE FROM runs_fts WHERE id = ?", (run_id,))
            conn.execute(
                "INSERT INTO runs_fts(id, name, narrative_text) VALUES (?, ?, ?)",
                (run_id, display, narr_text),
            )
        conn.commit()
    return get_run(run_id, db_path=db_path, full=True)


def write_trace_jsonl(
    run_id: str,
    records: List[Dict[str, Any]],
    *,
    runs_root: Optional[Path] = None,
) -> Path:
    path = trace_path(run_id, runs_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, separators=(",", ":"), ensure_ascii=False))
            handle.write("\n")
    return path


def load_trace_records(
    run_id: str,
    *,
    runs_root: Optional[Path] = None,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    path = trace_path(run_id, runs_root)
    if not path.exists():
        return []
    records: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            records.append(json.loads(line))
            if limit is not None and len(records) >= limit:
                break
    return records


def save_json_artifact(
    run_id: str,
    filename: str,
    payload: Any,
    *,
    runs_root: Optional[Path] = None,
) -> Path:
    path = run_dir(run_id, runs_root) / filename
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def load_json_artifact(
    run_id: str,
    filename: str,
    *,
    runs_root: Optional[Path] = None,
) -> Any:
    path = run_dir(run_id, runs_root) / filename
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
