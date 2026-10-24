import pytest

from vitality_client.cli.duration import parse_duration


@pytest.mark.parametrize("text,secs", [("30m", 1800.0), ("2h", 7200.0), ("45s", 45.0)])
def test_parse_duration(text, secs): assert parse_duration(text) == secs


@pytest.mark.parametrize("bad", ["", "30", "m", "-5m", "1.5h", "3d"])
def test_parse_duration_rejects(bad):
    with pytest.raises(ValueError, match="invalid duration"): parse_duration(bad)
