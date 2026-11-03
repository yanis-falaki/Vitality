import base64
import json
import re
from pathlib import Path

import pytest
from captured import make_captured, make_interaction

from vitality_observer.config import ObserverConfig
from vitality_observer.request import (
    PROMPT,
    PROMPT_VERSION,
    REPLY_SCHEMA,
    build_params,
    local_span,
    parse_reply,
    read_captured,
)


def test_params_shape(tmp_path: Path):
    captured = make_captured("2026-11-03T14:22:05Z")
    interaction = make_interaction(tmp_path, captured)
    config = ObserverConfig(model="claude-test", effort="medium")

    params = build_params(interaction, config)

    assert params["model"] == "claude-test"
    assert params["max_tokens"] == 16000
    assert params["system"] == PROMPT
    assert params["output_config"] == {
        "format": {"type": "json_schema", "schema": REPLY_SCHEMA},
        "effort": "medium",
    }
    assert "betas" not in params and "timeout" not in params and "fallbacks" not in params
    (message,) = params["messages"]
    assert message["role"] == "user"
    content = message["content"]
    assert content[0] == {
        "type": "text",
        "text": "Interaction laptop/2026-11-03/14-22-05 on laptop, from 2026-11-03T09:22:05-05:00 "
        "to 2026-11-03T09:22:12-05:00 (America/Toronto).",
    }
    assert content[1] == {"type": "text", "text": "0000.webp, 0 s after the first input:"}
    assert content[2]["type"] == "image"
    assert content[2]["source"] == {
        "type": "base64",
        "media_type": "image/webp",
        "data": base64.b64encode(b"fake-0000.webp").decode(),
    }
    assert content[3] == {"type": "text", "text": "0007.webp, 7 s after the first input:"}
    assert content[4]["source"]["data"] == base64.b64encode(b"fake-0007.webp").decode()
    assert len(content) == 5


def test_second_shot_in_same_second_uses_its_seconds(tmp_path: Path):
    captured = make_captured("2026-11-03T14:22:05Z", seconds=(0, 14))
    captured = captured.model_copy(update={"shots": [*captured.shots, captured.shots[-1].model_copy(update={"file": "0014-2.webp"})]})
    interaction = make_interaction(tmp_path, captured)

    content = build_params(interaction, ObserverConfig())["messages"][0]["content"]

    assert content[-2]["text"] == "0014-2.webp, 14 s after the first input:"


def test_local_span_dst():
    before = make_captured("2026-11-01T05:30:00Z")
    after = make_captured("2026-11-01T06:30:00Z")

    assert local_span(before)[0].isoformat() == "2026-11-01T01:30:00-04:00"
    assert local_span(after)[0].isoformat() == "2026-11-01T01:30:00-05:00"


def test_other_device_time_zone():
    captured = make_captured("2026-11-03T14:22:05Z", tz="America/Los_Angeles")

    start, end = local_span(captured)

    assert start.isoformat() == "2026-11-03T06:22:05-08:00"
    assert end.isoformat() == "2026-11-03T06:22:12-08:00"


def test_missing_screenshot(tmp_path: Path):
    captured = make_captured("2026-11-03T14:22:05Z", seconds=(0, 4))
    interaction = make_interaction(tmp_path, captured)
    (interaction.folder / "0004.webp").unlink()

    with pytest.raises(ValueError, match=re.escape("0004.webp")):
        build_params(interaction, ObserverConfig())


def test_build_params_uses_a_captured_already_read(tmp_path: Path):
    captured = make_captured("2026-11-03T14:22:05Z")
    interaction = make_interaction(tmp_path, captured)
    (interaction.folder / "interaction.json").unlink()      # not read again
    assert build_params(interaction, ObserverConfig(), captured) == build_params(
        make_interaction(tmp_path / "other", captured), ObserverConfig())


def test_screenshot_name_without_digits(tmp_path: Path):
    captured = make_captured("2026-11-03T14:22:05Z", seconds=(0,))
    interaction = make_interaction(tmp_path, captured)
    shot = captured.shots[0].model_copy(update={"file": "shot.webp"})
    (interaction.folder / "shot.webp").write_bytes(b"x")
    odd = captured.model_copy(update={"shots": [shot]})
    with pytest.raises(ValueError, match=re.escape("screenshot file shot.webp does not start with digits")):
        build_params(interaction, ObserverConfig(), odd)


def test_read_captured_rejects_missing_and_invalid(tmp_path: Path):
    captured = make_captured("2026-11-03T14:22:05Z")
    interaction = make_interaction(tmp_path, captured)
    assert read_captured(interaction) == captured

    (interaction.folder / "interaction.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="interaction.json"):
        read_captured(interaction)

    (interaction.folder / "interaction.json").unlink()
    with pytest.raises(ValueError, match="interaction.json"):
        read_captured(interaction)


GOOD = {"transcript": "Terminal open.", "operations": [{"text": "Had a terminal open", "confidence": 8}]}


def test_parse_reply_accepts():
    reply = parse_reply(json.dumps(GOOD))

    assert reply.transcript == "Terminal open."
    assert reply.operations[0].text == "Had a terminal open"
    assert reply.operations[0].confidence == 8


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        "[]",
        json.dumps({**GOOD, "operations": []}),
        json.dumps({**GOOD, "operations": [{"text": "x", "confidence": 0}]}),
        json.dumps({**GOOD, "operations": [{"text": "x", "confidence": 11}]}),
        json.dumps({**GOOD, "operations": [{"text": "  ", "confidence": 5}]}),
        json.dumps({**GOOD, "transcript": "  \n"}),
        json.dumps({**GOOD, "extra": 1}),
        json.dumps({"transcript": "x"}),
    ],
)
def test_parse_reply_rejects(text: str):
    with pytest.raises(ValueError) as error:
        parse_reply(text)
    assert "\n" not in str(error.value)


def test_prompt_version_is_stable():
    assert re.fullmatch(r"[0-9a-f]{12}", PROMPT_VERSION)
    from importlib import reload

    import vitality_observer.request as request

    reload(request)
    assert request.PROMPT_VERSION == PROMPT_VERSION
    assert PROMPT.startswith("# Observe one interaction")
