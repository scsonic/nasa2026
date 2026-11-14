"""SQLite store: one row per generation, keyed by a 5-letter share code."""
from __future__ import annotations

import json
import secrets
import sqlite3
import string
import threading
import time
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS generations (
    code             TEXT PRIMARY KEY,
    svs_id           INTEGER NOT NULL,
    svs_title        TEXT,
    search_term      TEXT,
    status           TEXT NOT NULL,
    stage_started_at REAL,
    error            TEXT,
    svs_json         TEXT,
    analysis_json    TEXT,
    tracks_json      TEXT NOT NULL,
    result_json      TEXT,
    client_ip        TEXT,
    created_at       REAL NOT NULL,
    updated_at       REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_generations_created ON generations(created_at);
CREATE INDEX IF NOT EXISTS idx_generations_svs ON generations(svs_id);
CREATE INDEX IF NOT EXISTS idx_generations_ip ON generations(client_ip, created_at);
"""

JSON_COLUMNS = {"svs_json", "analysis_json", "tracks_json", "result_json"}
ALPHABET = string.ascii_lowercase
TERMINAL = {"done", "failed"}


def new_code() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(5))


class DB:
    def __init__(self, path: Path):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self.lock = threading.Lock()

    def _row(self, row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        out = dict(row)
        for col in JSON_COLUMNS:
            out[col] = json.loads(out[col]) if out.get(col) else None
        return out

    def create(self, svs_id: int, svs_title: str | None, search_term: str | None,
               tracks: dict[str, Any], client_ip: str | None) -> str:
        now = time.time()
        with self.lock:
            for _ in range(20):
                code = new_code()
                try:
                    self.conn.execute(
                        "INSERT INTO generations (code, svs_id, svs_title, search_term, status, stage_started_at,"
                        " tracks_json, client_ip, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (code, svs_id, svs_title, search_term, "queued", now, json.dumps(tracks), client_ip, now, now),
                    )
                    self.conn.commit()
                    return code
                except sqlite3.IntegrityError:
                    continue
        raise RuntimeError("Could not allocate a unique share code")

    def get(self, code: str) -> dict[str, Any] | None:
        with self.lock:
            row = self.conn.execute("SELECT * FROM generations WHERE code = ?", (code,)).fetchone()
        return self._row(row)

    def update(self, code: str, **fields: Any) -> None:
        if not fields:
            return
        fields["updated_at"] = time.time()
        cols, vals = [], []
        for key, value in fields.items():
            cols.append(f"{key} = ?")
            vals.append(json.dumps(value) if key in JSON_COLUMNS and value is not None else value)
        vals.append(code)
        with self.lock:
            self.conn.execute(f"UPDATE generations SET {', '.join(cols)} WHERE code = ?", vals)
            self.conn.commit()

    def set_stage(self, code: str, status: str, **extra: Any) -> None:
        self.update(code, status=status, stage_started_at=time.time(), **extra)

    def update_track(self, code: str, track: str, **fields: Any) -> None:
        """Read-modify-write one entry of tracks_json atomically."""
        with self.lock:
            row = self.conn.execute("SELECT tracks_json FROM generations WHERE code = ?", (code,)).fetchone()
            if row is None:
                return
            tracks = json.loads(row["tracks_json"])
            entry = tracks.setdefault(track, {})
            if "stage" in fields and fields["stage"] != entry.get("stage"):
                fields.setdefault("stage_started_at", time.time())
            entry.update(fields)
            self.conn.execute(
                "UPDATE generations SET tracks_json = ?, updated_at = ? WHERE code = ?",
                (json.dumps(tracks), time.time(), code),
            )
            self.conn.commit()

    def ip_usage_since(self, ip: str | None, ts: float) -> tuple[int, float | None]:
        """(number of generations from this IP since ts, created_at of the oldest of them)."""
        with self.lock:
            row = self.conn.execute(
                "SELECT COUNT(*), MIN(created_at) FROM generations WHERE client_ip IS ? AND created_at >= ?",
                (ip, ts),
            ).fetchone()
        return row[0], row[1]

    def summaries(self, codes: list[str]) -> list[dict[str, Any]]:
        """Short rows for the browser's play history (codes come from its cookie)."""
        if not codes:
            return []
        marks = ",".join("?" * len(codes))
        with self.lock:
            rows = self.conn.execute(
                f"SELECT code, svs_id, svs_title, status, created_at, svs_json FROM generations WHERE code IN ({marks})",
                codes,
            ).fetchall()
        out = []
        for r in rows:
            svs = json.loads(r["svs_json"]) if r["svs_json"] else {}
            out.append({"code": r["code"], "svs_id": r["svs_id"], "title": r["svs_title"] or svs.get("title"),
                        "status": r["status"], "created_at": r["created_at"], "image_url": svs.get("image_url")})
        return out

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.conn.execute(
                "SELECT code, svs_id, svs_title, status, created_at FROM generations ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def fail_unfinished(self, reason: str) -> int:
        """On startup, anything still running belonged to a process that died."""
        with self.lock:
            rows = self.conn.execute(
                "SELECT code, tracks_json FROM generations WHERE status NOT IN ('done','failed') OR tracks_json LIKE '%\"status\": \"working\"%'"
                " OR tracks_json LIKE '%\"status\": \"queued\"%'"
            ).fetchall()
            for row in rows:
                tracks = json.loads(row["tracks_json"])
                for entry in tracks.values():
                    if entry.get("status") in {"working", "queued"}:
                        entry.update(status="failed", stage="failed", error=reason)
                self.conn.execute(
                    "UPDATE generations SET tracks_json = ?, updated_at = ?,"
                    " status = CASE WHEN status IN ('done','failed') THEN status ELSE 'failed' END,"
                    " error = CASE WHEN status IN ('done','failed') THEN error ELSE ? END WHERE code = ?",
                    (json.dumps(tracks), time.time(), reason, row["code"]),
                )
            self.conn.commit()
        return len(rows)
