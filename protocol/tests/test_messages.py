import pytest
from pydantic import ValidationError

from vitality_protocol.messages import Interaction, Shot, UploadReply

SHOT = {"file": "0000.webp", "ts": "2026-10-25T17:00:00.449Z", "trigger": "activity",
        "width": 1920, "height": 1080, "bytes": 104052}
INTERACTION = {"name": "2026-10-25/17-00-00", "source": "screen",
               "start": "2026-10-25T17:00:00.449Z", "end": "2026-10-25T17:00:04.120Z",
               "local_start": "2026-10-25T10:00:00.449-07:00", "tz": "America/Vancouver",
               "shots": [SHOT]}


def test_interaction_round_trip():
    i = Interaction.model_validate(INTERACTION)
    assert Interaction.model_validate_json(i.model_dump_json()) == i
    offset = i.shots[0].ts.utcoffset()
    assert offset is not None and offset.total_seconds() == 0


@pytest.mark.parametrize("change", [
    {"name": "01M5R9694KWX42H5VG73MBPDNF"},
    {"source": "mic"},
    {"shots": []},
    {"start": "2026-10-25T17:00:00"},          # naive
    {"extra": 1},
])
def test_invalid_interaction_rejected(change):
    with pytest.raises(ValidationError):
        Interaction.model_validate({**INTERACTION, **change})


@pytest.mark.parametrize("change", [{"file": "../x.webp"}, {"trigger": "before"}])
def test_invalid_shot_rejected(change):
    with pytest.raises(ValidationError):
        Shot.model_validate({**SHOT, **change})


def test_upload_reply_defaults_ok():
    assert UploadReply(stored="new").model_dump() == {"ok": True, "stored": "new"}
