"""SQLite metadata index: interactions and their screenshots."""

import sqlite3
import threading
from pathlib import Path

from vitality_protocol.messages import Interaction, Shot

from vitality_client.daemon.storage.interface import (
    CaptureIndex,
    InteractionRow,
    ShotRow,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS interactions (
    name         TEXT PRIMARY KEY,
    source       TEXT NOT NULL,
    start        TEXT NOT NULL,
    end          TEXT,
    local_start  TEXT NOT NULL,
    tz           TEXT NOT NULL,
    uploaded_at  TEXT,
    upload_error TEXT
);
CREATE TABLE IF NOT EXISTS shots (
    interaction TEXT NOT NULL REFERENCES interactions(name),
    file        TEXT NOT NULL,
    ts          TEXT NOT NULL,
    trigger     TEXT NOT NULL CHECK (trigger IN ('activity','manual')),
    width       INTEGER NOT NULL,
    height      INTEGER NOT NULL,
    bytes       INTEGER NOT NULL,
    PRIMARY KEY (interaction, file)
);
CREATE INDEX IF NOT EXISTS idx_shots_ts ON shots(ts);
CREATE INDEX IF NOT EXISTS idx_interactions_uploaded_at ON interactions(uploaded_at);
"""

_PENDING = "end IS NOT NULL AND uploaded_at IS NULL AND upload_error IS NULL"


class Store(CaptureIndex):
    """Thread-safe: the writer thread inserts while the event loop reads."""

    def __init__(self, db_path: Path) -> None:
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)

    def insert_shot(self, shot: ShotRow, new_interaction: InteractionRow | None = None) -> None:
        with self._lock, self._conn:  # one transaction: commits on success, rolls back on error
            if new_interaction is not None:
                i = new_interaction
                self._conn.execute(
                    "INSERT INTO interactions (name, source, start, end, local_start, tz, "
                    "uploaded_at, upload_error) VALUES (?, ?, ?, NULL, ?, ?, NULL, NULL)",
                    (i.name, i.source, i.start, i.local_start, i.tz),
                )
            self._conn.execute(
                "INSERT INTO shots (interaction, file, ts, trigger, width, height, bytes) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (shot.interaction, shot.file, shot.ts, shot.trigger,
                 shot.width, shot.height, shot.bytes),
            )

    def has_interaction(self, name: str) -> bool:
        with self._lock:
            return self._conn.execute(
                "SELECT 1 FROM interactions WHERE name = ?", (name,)
            ).fetchone() is not None

    def shot_files(self, name: str) -> set[str]:
        with self._lock:
            return {r[0] for r in self._conn.execute(
                "SELECT file FROM shots WHERE interaction = ?", (name,))}

    def end_interaction(self, name: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE interactions SET end = "
                "(SELECT MAX(ts) FROM shots WHERE interaction = ?) WHERE name = ?",
                (name, name),
            )

    def open_interactions(self) -> list[str]:
        with self._lock:
            return [r[0] for r in self._conn.execute(
                "SELECT name FROM interactions WHERE end IS NULL ORDER BY start, name")]

    def interaction(self, name: str) -> Interaction:
        with self._lock:
            head = self._conn.execute(
                "SELECT source, start, end, local_start, tz FROM interactions WHERE name = ?",
                (name,),
            ).fetchone()
            if head is None:
                raise KeyError(name)
            shots = self._conn.execute(
                "SELECT file, ts, trigger, width, height, bytes FROM shots "
                "WHERE interaction = ? ORDER BY ts, file", (name,),
            ).fetchall()
        source, start, end, local_start, tz = head
        return Interaction(
            name=name, source=source, start=start, end=end, local_start=local_start, tz=tz,
            shots=[Shot(file=f, ts=t, trigger=tr, width=w, height=h, bytes=b)
                   for f, t, tr, w, h, b in shots],
        )

    def shot_paths(self) -> set[str]:
        with self._lock:
            return {f"captures/{i}/{f}" for i, f in self._conn.execute(
                "SELECT interaction, file FROM shots")}

    def count_shots_since(self, ts: str) -> int:
        with self._lock:
            return self._conn.execute(
                "SELECT COUNT(*) FROM shots WHERE ts >= ?", (ts,)
            ).fetchone()[0]

    def last_shot_ts(self) -> str | None:
        with self._lock:
            return self._conn.execute("SELECT MAX(ts) FROM shots").fetchone()[0]

    def pending_uploads(self) -> list[str]:
        with self._lock:
            return [r[0] for r in self._conn.execute(
                f"SELECT name FROM interactions WHERE {_PENDING} ORDER BY start, name")]

    def pending_count(self) -> int:
        with self._lock:
            return self._conn.execute(
                f"SELECT COUNT(*) FROM interactions WHERE {_PENDING}").fetchone()[0]

    def mark_uploaded(self, name: str, ts: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE interactions SET uploaded_at = ? WHERE name = ?", (ts, name))

    def mark_failed(self, name: str, error: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE interactions SET upload_error = ? WHERE name = ?", (error, name))

    def close(self) -> None:
        with self._lock:
            self._conn.close()
