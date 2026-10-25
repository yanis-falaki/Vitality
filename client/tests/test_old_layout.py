import os
import sqlite3

import pytest

from vitality_client.daemon.storage import OldLayoutError, Store, check_layout

OLD_SHOT = "captures/2025-10-21/01JAB0000000000000000000.webp"


def snapshot(root):
    """{relative path: (size, mtime_ns)} for every file under `root`."""
    return {p.relative_to(root).as_posix(): (p.stat().st_size, p.stat().st_mtime_ns)
            for p in root.rglob("*") if p.is_file()}


def old_index(data):
    """An index.sqlite as the pre-interaction daemon left it (WAL mode, `captures` table)."""
    data.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(data / "index.sqlite")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE captures (capture_id TEXT PRIMARY KEY, path TEXT NOT NULL)")
    conn.execute("INSERT INTO captures VALUES ('01JAB', ?)", (OLD_SHOT,))
    conn.commit()
    conn.close()


def test_old_captures_table_is_refused_and_nothing_is_touched(tmp_path):
    old_index(tmp_path)
    before = snapshot(tmp_path)
    with pytest.raises(OldLayoutError, match="run the one-time migration first"):
        check_layout(tmp_path)
    assert snapshot(tmp_path) == before          # no -wal/-shm left behind, nothing rewritten


def test_old_captures_table_is_seen_while_another_process_has_it_open(tmp_path):
    old_index(tmp_path)
    live = sqlite3.connect(tmp_path / "index.sqlite")
    live.execute("PRAGMA journal_mode=WAL")
    live.execute("INSERT INTO captures VALUES ('01JAC', 'x')")
    live.commit()                                # the -wal file now exists
    try:
        with pytest.raises(OldLayoutError):
            check_layout(tmp_path)
    finally:
        live.close()


def test_screenshot_at_the_old_depth_is_refused_and_nothing_is_touched(tmp_path):
    shot = tmp_path / OLD_SHOT
    shot.parent.mkdir(parents=True); shot.write_bytes(b"RIFF....WEBP")
    before = snapshot(tmp_path)
    with pytest.raises(OldLayoutError, match="old capture layout found"):
        check_layout(tmp_path)
    assert snapshot(tmp_path) == before


def test_new_layout_passes(tmp_path):
    Store(tmp_path / "index.sqlite").close()
    shot = tmp_path / "captures/2025-10-25/17-00-00/0000.webp"
    shot.parent.mkdir(parents=True); shot.write_bytes(b"x")
    check_layout(tmp_path)


def test_missing_data_dir_passes_and_is_not_created(tmp_path):
    check_layout(tmp_path / "nope")
    assert not os.path.exists(tmp_path / "nope")
