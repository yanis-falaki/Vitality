import os
import stat
from datetime import UTC, datetime

import pytest
from vitality_server.shared.storage import DeviceSummary, FileStore, read_summary

from stored import WEBP, store_interaction


def at(hour, minute=0, second=0, day=25):
    return datetime(2026, 10, day, hour, minute, second, tzinfo=UTC)


def make_store(tmp_path):
    store = FileStore(tmp_path)
    for client, start, shots, received in [
        ("laptop", "2026-10-25T17:00:00Z", 2, at(17, 0, 5)),
        ("laptop", "2026-10-25T18:00:00Z", 3, at(18, 0, 7)),
        ("desk", "2026-10-24T09:00:00Z", 1, at(9, day=24)),
    ]:
        store_interaction(store, client, start, shots, received)


def test_one_summary_per_client_sorted_by_name(tmp_path):
    make_store(tmp_path)
    assert read_summary(tmp_path) == [
        DeviceSummary("desk", interactions=1, shots=1, bytes=len(WEBP),
                      last_received="2026-10-24T09:00:00.000Z"),
        DeviceSummary("laptop", interactions=2, shots=5, bytes=5 * len(WEBP),
                      last_received="2026-10-25T18:00:07.000Z"),
    ]


def test_an_empty_store_gives_no_summaries(tmp_path):
    FileStore(tmp_path)
    assert read_summary(tmp_path) == []


def test_a_data_folder_without_captures_gives_no_summaries_and_creates_nothing(tmp_path):
    assert read_summary(tmp_path) == []
    assert list(tmp_path.iterdir()) == []


def test_a_missing_data_folder_raises_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        read_summary(tmp_path / "nowhere")


def test_folders_without_interaction_json_and_other_files_are_not_counted(tmp_path):
    make_store(tmp_path)
    (tmp_path / "captures/laptop/2026-10-25/19-00-00").mkdir()             # not a complete upload
    (tmp_path / "captures/laptop/2026-10-25/17-00-00/notes.txt").write_text("x")
    (tmp_path / "captures/empty").mkdir()                                  # a client with nothing yet
    assert [(d.client, d.interactions, d.shots) for d in read_summary(tmp_path)] == [
        ("desk", 1, 1), ("laptop", 2, 5)]


def test_reading_changes_nothing(tmp_path):
    make_store(tmp_path)
    before = sorted((str(p), p.stat().st_mtime_ns) for p in tmp_path.rglob("*"))
    read_summary(tmp_path)
    assert sorted((str(p), p.stat().st_mtime_ns) for p in tmp_path.rglob("*")) == before


def test_works_when_the_folders_are_read_only(tmp_path):
    make_store(tmp_path)
    folders = [tmp_path, *(p for p in tmp_path.rglob("*") if p.is_dir())]
    for p in folders:
        p.chmod(0o555)
    try:
        assert [d.client for d in read_summary(tmp_path)] == ["desk", "laptop"]
    finally:
        for p in folders:
            p.chmod(stat.S_IRWXU)


def test_unreadable_folder_raises_permission_error(tmp_path):
    make_store(tmp_path)
    tmp_path.chmod(0o000)
    try:
        if os.access(tmp_path, os.R_OK):
            pytest.skip("running as a user that ignores directory permissions")
        with pytest.raises(PermissionError):
            read_summary(tmp_path)
    finally:
        tmp_path.chmod(stat.S_IRWXU)
