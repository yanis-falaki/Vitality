import base64
import json
import re
from pathlib import Path

import pytest
from builders import make_interaction, make_item

from vitality_observer.config import ObserverConfig
from vitality_observer.request import (
    PROMPT,
    PROMPT_VERSION,
    REPLY_SCHEMA,
    build_params,
    local_span,
    parse_reply,
    read_interaction,
)


def test_params_shape(tmp_path: Path):
    interaction = make_interaction("2026-11-03T14:22:05Z")
    item = make_item(tmp_path, interaction)
    config = ObserverConfig(model="claude-test", effort="medium")

    params = build_params(item, interaction, config)

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
    interaction = make_interaction("2026-11-03T14:22:05Z", seconds=(0, 14))
    interaction = interaction.model_copy(update={"shots": [*interaction.shots, interaction.shots[-1].model_copy(update={"file": "0014-2.webp"})]})
    item = make_item(tmp_path, interaction)

    content = build_params(item, interaction, ObserverConfig())["messages"][0]["content"]

    assert content[-2]["text"] == "0014-2.webp, 14 s after the first input:"


def test_local_span_dst():
    before = make_interaction("2026-11-01T05:30:00Z")
    after = make_interaction("2026-11-01T06:30:00Z")

    assert local_span(before)[0].isoformat() == "2026-11-01T01:30:00-04:00"
    assert local_span(after)[0].isoformat() == "2026-11-01T01:30:00-05:00"


def test_other_device_time_zone():
    interaction = make_interaction("2026-11-03T14:22:05Z", tz="America/Los_Angeles")

    start, end = local_span(interaction)

    assert start.isoformat() == "2026-11-03T06:22:05-08:00"
    assert end.isoformat() == "2026-11-03T06:22:12-08:00"


def test_missing_screenshot(tmp_path: Path):
    interaction = make_interaction("2026-11-03T14:22:05Z", seconds=(0, 4))
    item = make_item(tmp_path, interaction)
    (item.folder / "0004.webp").unlink()

    with pytest.raises(ValueError, match=re.escape("0004.webp")):
        build_params(item, interaction, ObserverConfig())


def test_build_params_does_not_read_interaction_json(tmp_path: Path):
    interaction = make_interaction("2026-11-03T14:22:05Z")
    item = make_item(tmp_path, interaction)
    (item.folder / "interaction.json").unlink()
    build_params(item, interaction, ObserverConfig())


def test_screenshot_name_without_digits(tmp_path: Path):
    interaction = make_interaction("2026-11-03T14:22:05Z", seconds=(0,))
    item = make_item(tmp_path, interaction)
    shot = interaction.shots[0].model_copy(update={"file": "shot.webp"})
    (item.folder / "shot.webp").write_bytes(b"x")
    odd = interaction.model_copy(update={"shots": [shot]})
    with pytest.raises(ValueError, match=re.escape("screenshot file shot.webp does not start with digits")):
        build_params(item, odd, ObserverConfig())


def test_read_interaction_rejects_missing_and_invalid(tmp_path: Path):
    interaction = make_interaction("2026-11-03T14:22:05Z")
    item = make_item(tmp_path, interaction)
    assert read_interaction(item) == interaction

    (item.folder / "interaction.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="interaction.json"):
        read_interaction(item)

    (item.folder / "interaction.json").unlink()
    with pytest.raises(ValueError, match="interaction.json"):
        read_interaction(item)


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


def test_screen_labels_number_the_screen_and_give_its_role(tmp_path: Path):
    interaction = make_interaction("2026-11-03T14:22:05Z", seconds=(0, 0, 12))
    first, second, third = interaction.shots
    shots = [first.model_copy(update={"screen": 1, "screen_position": (0, 0), "focused": True}),
             second.model_copy(update={"file": "0000-2.webp", "screen": 2,
                                       "screen_position": (0, 1080), "focused": False}),
             third.model_copy(update={"screen": 2, "screen_position": (0, 1080)})]
    interaction = interaction.model_copy(update={"shots": shots})
    item = make_item(tmp_path, interaction)

    content = build_params(item, interaction, ObserverConfig())["messages"][0]["content"]

    assert content[0]["text"].endswith(
        "(America/Toronto). Screens (top-left corner, logical pixels): 1 at (0, 0), 2 at (0, 1080).")
    assert [c["text"] for c in content[1::2]] == [
        "0000.webp, 0 s after the first input · screen 1 of 2 · focused:",
        "0000-2.webp, 0 s after the first input · screen 2 of 2 · other:",
        "0012.webp, 12 s after the first input · screen 2 of 2:",
    ]


def test_screens_without_positions_give_no_layout(tmp_path: Path):
    interaction = make_interaction("2026-11-03T14:22:05Z", seconds=(0, 0))
    first, second = interaction.shots
    shots = [first.model_copy(update={"screen": 1}),
             second.model_copy(update={"file": "0000-2.webp", "screen": 2})]
    interaction = interaction.model_copy(update={"shots": shots})
    item = make_item(tmp_path, interaction)

    content = build_params(item, interaction, ObserverConfig())["messages"][0]["content"]

    assert "Screens (" not in content[0]["text"] and content[0]["text"].endswith("(America/Toronto).")
    assert content[1]["text"] == "0000.webp, 0 s after the first input · screen 1 of 2:"


def test_prompt_explains_screens():
    assert "focused" in PROMPT and "several monitors" in PROMPT and "not left or right" in PROMPT
