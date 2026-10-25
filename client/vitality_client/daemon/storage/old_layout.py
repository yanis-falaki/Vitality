"""Detecting the capture layout from before interactions, which needs a one-time migration.

Old layout: index.sqlite has a `captures` table (one row per screenshot), and screenshots sit
directly in a date folder, `captures/2025-10-21/<ULID>.webp`. New layout: tables
`interactions` and `shots`, screenshots in `captures/<date>/<time>/`.
"""

import sqlite3
from pathlib import Path

from vitality_client.daemon.storage.interface import OldLayoutError


MESSAGE = "old capture layout found; run the one-time migration first (see client/README.md)"
"""The message of the `OldLayoutError` that `check_layout` raises."""


def check_layout(data_dir: Path) -> None:
    """Raise `OldLayoutError` if `data_dir` still has the old layout; return if not.

    Only reads: it creates, changes and deletes nothing (not even the directory, nor
    SQLite's -wal/-shm files). A missing data directory or index is a fresh start, not old.
    Raises `sqlite3.DatabaseError` if index.sqlite exists but is not a database.
    """
    data_dir = Path(data_dir)
    if _has_old_table(data_dir / "index.sqlite") or _has_old_shots(data_dir / "captures"):
        raise OldLayoutError(MESSAGE)


def _has_old_table(db: Path) -> bool:
    if not db.is_file():
        return False
    # A -wal file means a writer is live (or did not close cleanly): read through it.
    # Without one, everything is in the main file, and `immutable` keeps SQLite from
    # creating -wal/-shm files that a read-only connection could not remove again.
    wal = db.with_name(db.name + "-wal").exists()
    uri = db.resolve().as_uri() + ("?mode=ro" if wal else "?mode=ro&immutable=1")
    conn = sqlite3.connect(uri, uri=True)
    try:
        return conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'captures'"
        ).fetchone() is not None
    finally:
        conn.close()


def _has_old_shots(captures: Path) -> bool:
    return any(p.is_file() for p in captures.glob("*/*.webp"))
