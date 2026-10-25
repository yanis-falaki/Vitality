import json
import sqlite3
from unittest.mock import patch

import pytest
from PIL import Image

from vitality_client.daemon.storage import Store, Writer, format_ts, local_tz_name

img = Image.new("RGB", (64, 40), "white")
T0 = 1761411600.449                      # 2025-10-25T17:00:00.449Z


def make(tmp_path):
    store = Store(tmp_path / "index.sqlite")
    return store, Writer(tmp_path, store, 85, "America/Vancouver")


def test_shots_go_into_interaction_folder(tmp_path):
    store, w = make(tmp_path)
    a = w.write(img, T0, T0, "activity"); b = w.write(img, T0, T0 + 4.7, "activity")
    assert (a.path, b.path) == ("captures/2025-10-25/17-00-00/0000.webp",
                                "captures/2025-10-25/17-00-00/0004.webp")
    assert Image.open(tmp_path / b.path).format == "WEBP"
    assert (a.ts, a.trigger, a.width, a.height) == ("2025-10-25T17:00:00.449Z", "activity", 64, 40)
    assert a.bytes == (tmp_path / a.path).stat().st_size
    assert store.shot_paths() == {a.path, b.path}


def test_end_writes_interaction_json(tmp_path):
    store, w = make(tmp_path)
    w.write(img, T0, T0, "activity"); w.write(img, T0, T0 + 4.7, "manual")
    assert w.end(T0) == "2025-10-25/17-00-00"
    data = json.loads((tmp_path / "captures/2025-10-25/17-00-00/interaction.json").read_text())
    assert data["tz"] == "America/Vancouver" and data["local_start"].startswith("2025-10-25T10:00:00.449-07:00")
    assert [s["file"] for s in data["shots"]] == ["0000.webp", "0004.webp"]
    assert [s["trigger"] for s in data["shots"]] == ["activity", "manual"]
    assert data["end"] == "2025-10-25T17:00:05.149Z"
    assert store.pending_uploads() == ["2025-10-25/17-00-00"]
    assert not list(tmp_path.rglob("*.tmp"))


def test_end_without_shots_returns_none(tmp_path):
    _, w = make(tmp_path)
    assert w.end(T0) is None and not (tmp_path / "captures").exists()


def test_end_twice_returns_none(tmp_path):
    _, w = make(tmp_path)
    w.write(img, T0, T0, "activity")
    assert w.end(T0) is not None and w.end(T0) is None


def test_same_second_shot_gets_suffix(tmp_path):
    _, w = make(tmp_path)
    w.write(img, T0, T0 + 4.1, "activity")
    assert w.write(img, T0, T0 + 4.6, "manual").file == "0004-2.webp"


def test_shot_before_start_is_floored_at_zero(tmp_path):
    _, w = make(tmp_path)
    assert w.write(img, T0, T0 - 0.5, "activity").file == "0000.webp"


def test_name_clash_gets_suffix(tmp_path):                                   # Review Focus 2
    _, w = make(tmp_path)
    w.write(img, T0, T0, "activity"); w.end(T0)
    assert w.write(img, T0 + 0.3, T0 + 0.3, "activity").interaction == "2025-10-25/17-00-00-2"


def test_same_start_after_end_opens_new_interaction(tmp_path):
    _, w = make(tmp_path)
    w.write(img, T0, T0, "manual"); w.end(T0)
    assert w.write(img, T0, T0, "activity").interaction == "2025-10-25/17-00-00-2"


def test_failed_first_shot_leaves_no_interaction(tmp_path):                 # Review Focus 1
    store, w = make(tmp_path)
    with patch.object(store, "insert_shot", side_effect=sqlite3.OperationalError("disk full")):
        with pytest.raises(sqlite3.OperationalError): w.write(img, T0, T0, "activity")
    assert not store.has_interaction("2025-10-25/17-00-00") and w.end(T0) is None


def test_close_interrupted_ends_open_interactions(tmp_path):
    store, w = make(tmp_path)
    w.write(img, T0, T0, "activity")
    store2 = Store(tmp_path / "index.sqlite"); w2 = Writer(tmp_path, store2, 85, "UTC")
    assert w2.close_interrupted() == 1
    assert (tmp_path / "captures/2025-10-25/17-00-00/interaction.json").exists()
    assert store2.open_interactions() == []


def test_cleanup_removes_unindexed_files_and_empty_folders(tmp_path):
    _, w = make(tmp_path)
    stray = tmp_path / "captures/2025-10-25/17-09-09/0000.webp"; stray.parent.mkdir(parents=True)
    stray.write_bytes(b"x"); (stray.parent / "0001.webp.tmp").write_bytes(b"x")
    assert w.cleanup_orphans() == 2 and not stray.parent.exists()


def test_cleanup_keeps_indexed_files(tmp_path):
    _, w = make(tmp_path)
    shot = w.write(img, T0, T0, "activity"); w.end(T0)
    (tmp_path / "captures/2025-10-25/17-00-00/0001.webp.tmp").write_bytes(b"x")
    assert w.cleanup_orphans() == 1
    assert (tmp_path / shot.path).exists()
    assert (tmp_path / "captures/2025-10-25/17-00-00/interaction.json").exists()


def test_local_tz_name_falls_back_to_utc(monkeypatch, tmp_path):            # Review Focus 4
    monkeypatch.delenv("TZ", raising=False)
    with patch("vitality_client.daemon.storage.writer.LOCALTIME", tmp_path / "nope"):
        assert local_tz_name() == "UTC"


def test_local_tz_name_prefers_valid_env(monkeypatch):
    monkeypatch.setenv("TZ", "Europe/Paris")
    assert local_tz_name() == "Europe/Paris"


def test_local_tz_name_reads_localtime_symlink(monkeypatch, tmp_path):
    monkeypatch.setenv("TZ", "not/a-zone")
    link = tmp_path / "localtime"; link.symlink_to(tmp_path / "usr/share/zoneinfo/Asia/Tokyo")
    with patch("vitality_client.daemon.storage.writer.LOCALTIME", link):
        assert local_tz_name() == "Asia/Tokyo"


def test_oversized_image_scaled_to_fit(tmp_path):
    _, w = make(tmp_path)
    big = Image.new("RGB", (20000, 1000))
    row = w.write(big, 0.0, 0.0, "activity")
    assert row.width == 16383 and row.height == 819
    assert big.size == (20000, 1000)  # caller's image is not mutated


def test_failed_insert_leaves_no_row_and_cleanup_removes_file(tmp_path):     # Review Focus 5
    store, w = make(tmp_path)
    with patch.object(store, "insert_shot", side_effect=sqlite3.OperationalError("disk full")):
        with pytest.raises(sqlite3.OperationalError): w.write(img, 0.0, 0.0, "activity")
    assert not list(tmp_path.rglob("*.tmp"))
    assert w.cleanup_orphans() == 1 and not list(tmp_path.rglob("*.webp"))


def test_failed_encode_leaves_no_temp_file(tmp_path):                        # Review Focus 5
    _, w = make(tmp_path)
    with patch("PIL.Image.Image.save", side_effect=OSError("no space")):
        with pytest.raises(OSError): w.write(img, 0.0, 0.0, "activity")
    assert not list(tmp_path.rglob("*.tmp")) and not list(tmp_path.rglob("*.webp"))


def test_disk_usage(tmp_path):
    _, w = make(tmp_path)
    row = w.write(img, 0.0, 0.0, "activity"); assert w.disk_usage_bytes() == row.bytes


def test_cleanup_and_usage_without_captures_dir(tmp_path):
    _, w = make(tmp_path)
    assert w.cleanup_orphans() == 0 and w.disk_usage_bytes() == 0


@pytest.mark.parametrize("epoch,expected", [
    (1761055325.312, "2025-10-21T14:02:05.312Z"),
    (0.0, "1970-01-01T00:00:00.000Z"),
    (1.0009, "1970-01-01T00:00:01.000Z"),   # truncates, does not round
])
def test_format_ts(epoch, expected):
    assert format_ts(epoch) == expected
