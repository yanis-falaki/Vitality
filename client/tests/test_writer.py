import sqlite3
from unittest.mock import patch

import pytest
from PIL import Image

from vitality_client.storage import Store
from vitality_client.storage import Writer

img = Image.new("RGB", (64, 40), "white")


def test_write_creates_file_and_row(tmp_path):
    store = Store(tmp_path / "index.sqlite"); w = Writer(tmp_path, store, 85, lambda: "CAP1")
    row = w.write(img, "before", "INT1", 1761055325.312)
    assert row.path == "captures/2025-10-21/CAP1.webp"     # UTC date of the ts
    assert (row.kind, row.interaction_id, row.source, row.ts) == ("before", "INT1", "screen", "2025-10-21T14:02:05.312Z")
    f = tmp_path / row.path
    assert f.exists() and Image.open(f).format == "WEBP" and Image.open(f).size == (64, 40)
    assert row.bytes == f.stat().st_size and store.paths() == {row.path}


def test_oversized_image_scaled_to_fit(tmp_path):
    w = Writer(tmp_path, Store(tmp_path / "i.sqlite"), 85, lambda: "C")
    big = Image.new("RGB", (20000, 1000))
    row = w.write(big, "after", "I", 0.0)
    assert row.width == 16383 and row.height == 819
    assert big.size == (20000, 1000)  # caller's image is not mutated


def test_failed_insert_leaves_no_row_and_cleanup_removes_file(tmp_path):     # Review Focus 5
    store = Store(tmp_path / "i.sqlite"); w = Writer(tmp_path, store, 85, lambda: "C")
    with patch.object(store, "insert", side_effect=sqlite3.OperationalError("disk full")):
        with pytest.raises(sqlite3.OperationalError): w.write(img, "after", "I", 0.0)
    assert not list(tmp_path.rglob("*.tmp"))
    assert w.cleanup_orphans() == 1 and not list(tmp_path.rglob("*.webp"))


def test_failed_encode_leaves_no_temp_file(tmp_path):                        # Review Focus 5
    w = Writer(tmp_path, Store(tmp_path / "i.sqlite"), 85, lambda: "C")
    with patch("PIL.Image.Image.save", side_effect=OSError("no space")):
        with pytest.raises(OSError): w.write(img, "after", "I", 0.0)
    assert not list(tmp_path.rglob("*.tmp")) and not list(tmp_path.rglob("*.webp"))


def test_disk_usage(tmp_path):
    w = Writer(tmp_path, Store(tmp_path / "i.sqlite"), 85, lambda: "C")
    row = w.write(img, "after", "I", 0.0); assert w.disk_usage_bytes() == row.bytes


def test_cleanup_keeps_indexed_files_and_removes_stray_tmp(tmp_path):
    w = Writer(tmp_path, Store(tmp_path / "i.sqlite"), 85, lambda: "C")
    row = w.write(img, "after", "I", 0.0)
    stray = tmp_path / "captures" / "1970-01-01" / "X.webp.tmp"
    stray.write_bytes(b"partial")
    assert w.cleanup_orphans() == 1
    assert (tmp_path / row.path).exists() and not stray.exists()


def test_cleanup_and_usage_without_captures_dir(tmp_path):
    w = Writer(tmp_path, Store(tmp_path / "i.sqlite"), 85, lambda: "C")
    assert w.cleanup_orphans() == 0 and w.disk_usage_bytes() == 0


@pytest.mark.parametrize("epoch,expected", [
    (1761055325.312, "2025-10-21T14:02:05.312Z"),
    (0.0, "1970-01-01T00:00:00.000Z"),
    (1.0009, "1970-01-01T00:00:01.000Z"),   # truncates, does not round
])
def test_format_ts(epoch, expected):
    from vitality_client.storage import format_ts
    assert format_ts(epoch) == expected
