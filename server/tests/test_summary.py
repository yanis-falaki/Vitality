import os
import sqlite3
import stat
from datetime import UTC, datetime

import pytest
from vitality_server.storage import DeviceSummary, FileStore, read_summary

from stored import WEBP, store_interaction


def at(hour, minute=0, second=0, day=25):
    return datetime(2026, 10, day, hour, minute, second, tzinfo=UTC)


def make_store(tmp_path):
    for client, start, shots, received in [
        ("laptop", "2026-10-25T17:00:00Z", 2, at(17, 0, 5)),
        ("laptop", "2026-10-25T18:00:00Z", 3, at(18, 0, 7)),
        ("desk", "2026-10-24T09:00:00Z", 1, at(9, day=24)),
    ]:
        store = FileStore(tmp_path, clock=received.timestamp)
        store_interaction(store, client, start, shots)
        store.close()


def test_one_summary_per_client_sorted_by_name(tmp_path):
    make_store(tmp_path)
    assert read_summary(tmp_path) == [
        DeviceSummary("desk", interactions=1, shots=1, bytes=len(WEBP),
                      last_received="2026-10-24T09:00:00.000Z"),
        DeviceSummary("laptop", interactions=2, shots=5, bytes=5 * len(WEBP),
                      last_received="2026-10-25T18:00:07.000Z"),
    ]


def test_empty_index_gives_no_summaries(tmp_path):
    FileStore(tmp_path).close()
    assert read_summary(tmp_path) == []


def test_missing_index_raises_file_not_found_and_creates_nothing(tmp_path):
    with pytest.raises(FileNotFoundError):
        read_summary(tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_unreadable_index_raises_sqlite_error(tmp_path):
    (tmp_path / "index.sqlite").write_text("this is not a database")
    with pytest.raises(sqlite3.Error):
        read_summary(tmp_path)


def snapshot(folder):
    return {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in folder.iterdir() if p.is_file()}


def test_reading_changes_no_file_and_creates_no_wal_or_shm(tmp_path):
    make_store(tmp_path)
    before = snapshot(tmp_path)
    assert sorted(before) == ["index.sqlite"]
    read_summary(tmp_path)
    assert snapshot(tmp_path) == before


def test_works_when_the_folder_is_read_only(tmp_path):
    make_store(tmp_path)
    before = snapshot(tmp_path)
    tmp_path.chmod(0o555)
    try:
        if os.access(tmp_path, os.W_OK):
            pytest.skip("running as a user that ignores directory permissions")
        assert [d.client for d in read_summary(tmp_path)] == ["desk", "laptop"]
    finally:
        tmp_path.chmod(stat.S_IRWXU)
    assert snapshot(tmp_path) == before


def test_works_when_the_index_file_is_read_only(tmp_path):
    make_store(tmp_path)
    (tmp_path / "index.sqlite").chmod(0o444)
    tmp_path.chmod(0o555)
    try:
        assert [d.client for d in read_summary(tmp_path)] == ["desk", "laptop"]
    finally:
        tmp_path.chmod(stat.S_IRWXU)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["captures", "index.sqlite", "tmp"]


def test_works_while_a_store_is_open_and_writing(tmp_path):
    store = FileStore(tmp_path, clock=at(17).timestamp)
    store_interaction(store, "laptop", "2026-10-25T17:00:00Z")
    try:
        assert read_summary(tmp_path)[0].interactions == 1
        store_interaction(store, "laptop", "2026-10-25T17:05:00Z")
        assert read_summary(tmp_path)[0].interactions == 2
    finally:
        store.close()
