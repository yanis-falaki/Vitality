from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from vitality_usermodel.formats import (
    ActionMeta,
    FormatError,
    Operation,
    OperationsMeta,
    kind_of,
    parse,
    read_file,
    render,
    write_file,
)

EST = timezone(timedelta(hours=-5))


def _operations_meta() -> OperationsMeta:
    return OperationsMeta(
        interaction="laptop/2026-11-03/14-22-05",
        device="laptop",
        start=datetime(2026, 11, 3, 9, 22, 5, tzinfo=EST),
        end=datetime(2026, 11, 3, 9, 22, 31, tzinfo=EST),
        operations=[
            Operation(id="op1", text='Noted "deadline: Friday" # not a comment, café', confidence=8),
            Operation(id="op2", text="It's 'quoted': yes", confidence=7),
        ],
    )


def _action_meta(**changes: object) -> ActionMeta:
    fields: dict[str, object] = dict(
        id="2026-11-03/14-22-05",
        summary="Checking the portal",
        start=datetime(2026, 11, 3, 9, 22, 5, tzinfo=EST),
        end=datetime(2026, 11, 3, 9, 23, 40, tzinfo=EST),
        operations=["laptop/2026-11-03/14-22-05#op1"],
        confidence=7,
    )
    fields.update(changes)
    return ActionMeta.model_validate(fields)


def test_operations_round_trip():
    m = _operations_meta()
    assert parse(render(m), "operations")[0] == m


def test_times_keep_their_offset():
    m = _action_meta(start=datetime(2026, 11, 1, 1, 30, tzinfo=EST))
    text = render(m)
    assert "2026-11-01T01:30:00-05:00" in text
    assert parse(text, "action")[0] == m


def test_ids_render_as_flow_list():
    assert "\npart_of: [act-0042]\n" in render(_action_meta(part_of=["act-0042"]))


def test_body_round_trip():
    text = render(_action_meta(), "First paragraph.\n\nSecond.")
    meta, body = parse(text, "action")
    assert meta == _action_meta()
    assert body == "First paragraph.\n\nSecond."
    assert render(meta, body) == text


def test_unknown_field_rejected():
    text = render(_action_meta()).replace("confidence: 7\n", "confidence: 7\nmood: good\n")
    with pytest.raises(FormatError, match="mood"):
        parse(text, "action")


def test_confidence_out_of_range_rejected():
    text = render(_action_meta()).replace("confidence: 7", "confidence: 11")
    with pytest.raises(FormatError, match="confidence"):
        parse(text, "action")


def test_missing_frontmatter():
    with pytest.raises(FormatError, match="^missing frontmatter$"):
        parse("hello", "action")


def test_kind_of():
    assert kind_of("activities/act-0001.md") == "activity"
    assert kind_of("actions/2026-11-03/14-22-05.md") == "action"
    assert kind_of("user-context.md") is None
    assert kind_of("activities/notes.txt") is None


def test_write_file_is_atomic(tmp_path: Path):
    path = tmp_path / "operations" / "laptop" / "x.md"
    m = _operations_meta()
    write_file(path, m)
    assert list(path.parent.glob("*.tmp-*")) == []
    assert read_file(path, "operations")[0] == m


def test_read_file_names_the_path(tmp_path: Path):
    path = tmp_path / "bad.md"
    path.write_text("hello")
    with pytest.raises(FormatError, match="bad.md: missing frontmatter"):
        read_file(path, "action")
