from datetime import UTC, datetime, timedelta, timezone

import pytest

from vitality_protocol.layout import interaction_name, name_matches_start, shot_filename

T = datetime(2026, 10, 25, 17, 0, 0, 449000, tzinfo=UTC)


def test_interaction_name_is_utc_start_to_the_second():
    assert interaction_name(T) == "2026-10-25/17-00-00"


def test_interaction_name_converts_local_time_to_utc():
    local = T.astimezone(timezone(timedelta(hours=-7)))
    assert interaction_name(local) == "2026-10-25/17-00-00"


def test_interaction_name_suffix_on_clash():
    assert interaction_name(T, {"2026-10-25/17-00-00"}) == "2026-10-25/17-00-00-2"
    taken = {"2026-10-25/17-00-00", "2026-10-25/17-00-00-2"}
    assert interaction_name(T, taken) == "2026-10-25/17-00-00-3"


def test_interaction_name_rejects_naive_datetime():
    with pytest.raises(ValueError):
        interaction_name(datetime(2026, 10, 25, 17, 0, 0))


def test_name_matches_start_allows_suffix():
    assert name_matches_start("2026-10-25/17-00-00", T)
    assert name_matches_start("2026-10-25/17-00-00-2", T)
    assert not name_matches_start("2026-10-25/17-00-01", T)


def test_shot_filename():
    assert shot_filename(0) == "0000.webp"
    assert shot_filename(14) == "0014.webp"
    assert shot_filename(14, {"0014.webp"}) == "0014-2.webp"
    with pytest.raises(ValueError):
        shot_filename(-1)
