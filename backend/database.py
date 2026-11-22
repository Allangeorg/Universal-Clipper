from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.config import DB_PATH

_lock = threading.RLock()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    with _lock, connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                source_path TEXT NOT NULL,
                source_name TEXT NOT NULL,
                source_kind TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued',
                stage TEXT NOT NULL DEFAULT 'queued',
                progress REAL NOT NULL DEFAULT 0,
                message TEXT NOT NULL DEFAULT 'Queued',
                duration REAL,
                result_json TEXT,
                options_json TEXT NOT NULL DEFAULT '{}',
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS clips (
                id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                start REAL NOT NULL,
                end REAL NOT NULL,
                score REAL NOT NULL,
                components_json TEXT NOT NULL,
                title TEXT NOT NULL,
                reason TEXT NOT NULL,
                transcript TEXT NOT NULL DEFAULT '',
                selected INTEGER NOT NULL DEFAULT 1,
                output_path TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_clips_job ON clips(job_id);
            CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at DESC);
            """
        )
        # Forward-compatible migration for databases created by v0.1-v0.3.1.
        columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
        if "options_json" not in columns:
            conn.execute("ALTER TABLE jobs ADD COLUMN options_json TEXT NOT NULL DEFAULT '{}' ")


def create_job(job_id: str, source_path: str | Path, source_name: str, source_kind: str,
               options: dict[str, Any] | None = None) -> None:
    stamp = now_iso()
    with _lock, connect() as conn:
        conn.execute(
            "INSERT INTO jobs(id,source_path,source_name,source_kind,status,stage,progress,message,options_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (job_id, str(source_path), source_name, source_kind, "queued", "queued", 0, "Queued for analysis",
             json.dumps(options or {}, ensure_ascii=False), stamp, stamp),
        )


def update_job(job_id: str, **fields: Any) -> None:
    if "options" in fields and "options_json" not in fields:
        fields["options_json"] = json.dumps(fields.pop("options") or {}, ensure_ascii=False)
    allowed = {"source_path", "status", "stage", "progress", "message", "duration", "result_json", "options_json", "error", "source_name"}
    values = {key: value for key, value in fields.items() if key in allowed}
    if not values:
        return
    values["updated_at"] = now_iso()
    assignments = ",".join(f"{key}=?" for key in values)
    with _lock, connect() as conn:
        conn.execute(f"UPDATE jobs SET {assignments} WHERE id=?", (*values.values(), job_id))


def add_clips(job_id: str, clips: list[dict[str, Any]]) -> None:
    with _lock, connect() as conn:
        conn.execute("DELETE FROM clips WHERE job_id=?", (job_id,))
        for clip in clips:
            conn.execute(
                """INSERT INTO clips(id,job_id,start,end,score,components_json,title,reason,transcript,selected,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (clip["id"], job_id, clip["start"], clip["end"], clip["score"],
                 json.dumps(clip.get("components", {})), clip.get("title", "Untitled highlight"),
                 clip.get("reason", ""), clip.get("transcript", ""), int(clip.get("selected", True)), now_iso()),
            )


def get_job(job_id: str) -> dict[str, Any] | None:
    with _lock, connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        result["result"] = json.loads(result.pop("result_json") or "null")
        try:
            result["options"] = json.loads(result.pop("options_json", "{}") or "{}")
        except json.JSONDecodeError:
            result["options"] = {}
        result["clips"] = get_clips(job_id, conn)
        return result


def get_clips(job_id: str, conn: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
    own = conn is None
    if own:
        conn = connect()
    assert conn is not None
    rows = conn.execute("SELECT * FROM clips WHERE job_id=? ORDER BY score DESC,start ASC", (job_id,)).fetchall()
    clips: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["duration"] = max(0.0, float(item["end"]) - float(item["start"]))
        item["components"] = json.loads(item.pop("components_json"))
        item["selected"] = bool(item["selected"])
        clips.append(item)
    if own:
        conn.close()
    return clips


def list_jobs(limit: int = 30) -> list[dict[str, Any]]:
    with _lock, connect() as conn:
        rows = conn.execute(
            "SELECT id,source_name,status,stage,progress,message,duration,error,created_at,updated_at FROM jobs ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]


def set_clip_selection(job_id: str, clip_id: str, selected: bool) -> bool:
    with _lock, connect() as conn:
        cursor = conn.execute("UPDATE clips SET selected=? WHERE job_id=? AND id=?", (int(selected), job_id, clip_id))
        return cursor.rowcount > 0


def set_clip_output(clip_id: str, output_path: str) -> None:
    with _lock, connect() as conn:
        conn.execute("UPDATE clips SET output_path=? WHERE id=?", (output_path, clip_id))


def split_clip_record(job_id: str, clip_id: str, split_at: float) -> list[str] | None:
    """Replace a clip with two contiguous editable clip records."""
    import uuid
    with _lock, connect() as conn:
        row = conn.execute("SELECT * FROM clips WHERE id=? AND job_id=?", (clip_id, job_id)).fetchone()
        if not row:
            return None
        start, end = float(row["start"]), float(row["end"])
        if split_at <= start + 0.2 or split_at >= end - 0.2:
            raise ValueError("Split point must leave at least 0.2 seconds on both sides.")
        components = row["components_json"]
        base_title = row["title"] or "Highlight"
        new_ids = [uuid.uuid4().hex[:12], uuid.uuid4().hex[:12]]
        now = now_iso()
        rows = [
            (new_ids[0], job_id, start, split_at, row["score"], components, f"{base_title} · Part 1", row["reason"], row["transcript"], row["selected"], now),
            (new_ids[1], job_id, split_at, end, row["score"], components, f"{base_title} · Part 2", row["reason"], row["transcript"], row["selected"], now),
        ]
        conn.execute("DELETE FROM clips WHERE id=? AND job_id=?", (clip_id, job_id))
        conn.executemany(
            """INSERT INTO clips(id,job_id,start,end,score,components_json,title,reason,transcript,selected,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""", rows
        )
        return new_ids


def delete_job(job_id: str) -> bool:
    with _lock, connect() as conn:
        conn.execute("DELETE FROM clips WHERE job_id=?", (job_id,))
        cursor = conn.execute("DELETE FROM jobs WHERE id=?", (job_id,))
        return cursor.rowcount > 0

