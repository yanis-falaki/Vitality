"""SQLite metadata index: one row per saved screenshot."""

import sqlite3
import threading
from pathlib import Path

from vitality_client.storage.interface import CaptureIndex, CaptureRow

_SCHEMA = """
CREATE TABLE IF NOT EXISTS captures (
    capture_id     TEXT PRIMARY KEY,
    interaction_id TEXT NOT NULL,
    kind           TEXT NOT NULL CHECK (kind IN ('before','after')),
    source         TEXT NOT NULL,
    ts             TEXT NOT NULL,
    path           TEXT NOT NULL,
    width          INTEGER NOT NULL,
    height         INTEGER NOT NULL,
    bytes          INTEGER NOT NULL,
    uploaded_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_captures_ts ON captures(ts);
CREATE INDEX IF NOT EXISTS idx_captures_uploaded_at ON captures(uploaded_at);
"""


class Store(CaptureIndex):
    """Thread-safe: the writer thread inserts while the event loop reads."""

    def __init__(self, db_path: Path) -> None:
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)

    def insert(self, row: CaptureRow) -> None:
        with self._lock, self._conn:  # commits on success, rolls back on error
            self._conn.execute(
                "INSERT INTO captures (capture_id, interaction_id, kind, source, ts, "
                "path, width, height, bytes, uploaded_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
                (row.capture_id, row.interaction_id, row.kind, row.source, row.ts,
                 row.path, row.width, row.height, row.bytes),
            )

    def paths(self) -> set[str]:
        with self._lock:
            return {r[0] for r in self._conn.execute("SELECT path FROM captures")}

    def count_since(self, ts: str) -> int:
        with self._lock:
            return self._conn.execute(
                "SELECT COUNT(*) FROM captures WHERE ts >= ?", (ts,)
            ).fetchone()[0]

    def last_ts(self) -> str | None:
        with self._lock:
            return self._conn.execute("SELECT MAX(ts) FROM captures").fetchone()[0]

    def close(self) -> None:
        with self._lock:
            self._conn.close()
