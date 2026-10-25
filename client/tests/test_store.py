import sqlite3

import pytest

from vitality_client.daemon.storage import InteractionRow, ShotRow, Store

I = InteractionRow("2025-10-25/17-00-00", "2025-10-25T17:00:00.449Z",
                   "2025-10-25T10:00:00.449-07:00", "America/Vancouver")
S = ShotRow(I.name, "0000.webp", "2025-10-25T17:00:00.449Z", "activity", 1920, 1080, 1000)


def test_pending_upload_lifecycle(tmp_path):
    s = Store(tmp_path / "i.sqlite"); s.insert_shot(S, I)
    assert s.pending_uploads() == []                 # still open
    s.end_interaction(I.name); assert s.pending_uploads() == [I.name] and s.pending_count() == 1
    s.mark_uploaded(I.name, "2025-10-25T17:01:00.000Z"); assert s.pending_uploads() == []


def test_failed_interaction_is_not_pending(tmp_path):
    s = Store(tmp_path / "i.sqlite"); s.insert_shot(S, I); s.end_interaction(I.name)
    s.mark_failed(I.name, "422: bad name"); assert s.pending_uploads() == []


def test_pending_oldest_first(tmp_path):
    s = Store(tmp_path / "i.sqlite")
    late = InteractionRow("2025-10-25/18-00-00", "2025-10-25T18:00:00.000Z", I.local_start, I.tz)
    s.insert_shot(ShotRow(late.name, "0000.webp", late.start, "activity", 1, 1, 1), late)
    s.insert_shot(S, I); s.end_interaction(late.name); s.end_interaction(I.name)
    assert s.pending_uploads() == [I.name, late.name]


def test_interaction_model(tmp_path):
    s = Store(tmp_path / "i.sqlite"); s.insert_shot(S, I); s.end_interaction(I.name)
    m = s.interaction(I.name)
    assert m.name == I.name and m.end == m.start and [x.file for x in m.shots] == ["0000.webp"]


def test_counts_and_paths(tmp_path):
    s = Store(tmp_path / "i.sqlite"); s.insert_shot(S, I)
    assert s.count_shots_since("2025-10-25T00:00:00.000Z") == 1 and s.last_shot_ts() == S.ts
    assert s.shot_paths() == {"captures/2025-10-25/17-00-00/0000.webp"}


def test_open_interactions_and_shot_files(tmp_path):
    s = Store(tmp_path / "i.sqlite"); s.insert_shot(S, I)
    assert s.has_interaction(I.name) and not s.has_interaction("nope")
    assert s.open_interactions() == [I.name] and s.shot_files(I.name) == {"0000.webp"}
    s.end_interaction(I.name); assert s.open_interactions() == []


def test_first_shot_and_interaction_commit_together(tmp_path):
    s = Store(tmp_path / "i.sqlite"); s.insert_shot(S, I)
    dup = ShotRow(I.name, "0000.webp", S.ts, "manual", 1, 1, 1)
    with pytest.raises(sqlite3.IntegrityError): s.insert_shot(dup)
    other = InteractionRow("2025-10-25/19-00-00", S.ts, I.local_start, I.tz)
    with pytest.raises(sqlite3.IntegrityError):
        s.insert_shot(ShotRow(other.name, "0000.webp", S.ts, "bogus", 1, 1, 1), other)  # type: ignore[arg-type]
    assert not s.has_interaction(other.name)         # rolled back with the bad shot


def test_open_interaction_model_ends_at_last_shot(tmp_path):
    s = Store(tmp_path / "i.sqlite"); s.insert_shot(S, I)
    later = ShotRow(I.name, "0004.webp", "2025-10-25T17:00:04.100Z", "manual", 1, 1, 1)
    s.insert_shot(later)
    m = s.interaction(I.name)
    assert m.end.isoformat() == "2025-10-25T17:00:04.100000+00:00"
    assert s.open_interactions() == [I.name]          # reading does not end it


def test_shots_in_insertion_order_within_a_timestamp(tmp_path):
    s = Store(tmp_path / "i.sqlite"); s.insert_shot(S, I)
    a = ShotRow(I.name, "0004.webp", "2025-10-25T17:00:04.100Z", "activity", 1, 1, 1)
    b = ShotRow(I.name, "0004-2.webp", a.ts, "manual", 1, 1, 1)
    s.insert_shot(a); s.insert_shot(b)
    assert [x.file for x in s.interaction(I.name).shots] == ["0000.webp", "0004.webp", "0004-2.webp"]


def test_last_uploaded_at(tmp_path):
    s = Store(tmp_path / "i.sqlite"); assert s.last_uploaded_at() is None
    late = InteractionRow("2025-10-25/18-00-00", "2025-10-25T18:00:00.000Z", I.local_start, I.tz)
    s.insert_shot(S, I); s.insert_shot(ShotRow(late.name, "0000.webp", late.start, "activity", 1, 1, 1), late)
    s.end_interaction(I.name); s.end_interaction(late.name)
    assert s.last_uploaded_at() is None
    s.mark_uploaded(late.name, "2025-10-25T18:01:00.000Z"); s.mark_uploaded(I.name, "2025-10-25T18:02:00.000Z")
    assert s.last_uploaded_at() == "2025-10-25T18:02:00.000Z"
