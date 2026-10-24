import dataclasses
import sqlite3

import pytest

from vitality_client.daemon.storage import CaptureRow, Store

ROW = CaptureRow("01J...A", "01J...I", "before", "screen", "2026-10-21T14:02:05.312Z",
                 "captures/2026-10-21/01J...A.webp", 2560, 1600, 123456)


def test_insert_and_query(tmp_path):
    s = Store(tmp_path / "index.sqlite")
    s.insert(ROW)
    assert s.paths() == {ROW.path}
    assert s.count_since("2026-10-21T00:00:00.000Z") == 1
    assert s.count_since("2026-10-22T00:00:00.000Z") == 0
    assert s.last_ts() == ROW.ts


def test_uploaded_at_is_null(tmp_path):
    s = Store(tmp_path / "index.sqlite")
    s.insert(ROW)
    assert sqlite3.connect(tmp_path / "index.sqlite").execute("select uploaded_at from captures").fetchone() == (None,)


def test_schema_persists_across_reopen(tmp_path):
    Store(tmp_path / "index.sqlite").insert(ROW)
    assert Store(tmp_path / "index.sqlite").last_ts() == ROW.ts


def test_invalid_kind_rejected(tmp_path):
    with pytest.raises(sqlite3.IntegrityError):
        Store(tmp_path / "i.sqlite").insert(dataclasses.replace(ROW, kind="during"))
