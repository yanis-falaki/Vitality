import json
from datetime import datetime, timezone
from pathlib import Path

from vitality_usermodel.formats import (
    ActionMeta,
    ActivityMeta,
    ObservationMeta,
    Operation,
    OperationsMeta,
    StrivingMeta,
)
from vitality_usermodel.ids import (
    ACTION_ID,
    ACTIVITY_ID,
    INTERACTION,
    OPERATION_REF,
    STRIVING_ID,
    expected_path,
    issued,
    next_id,
)

T = datetime(2026, 11, 3, 14, 22, 5, tzinfo=timezone.utc)


def test_patterns() -> None:
    assert INTERACTION.fullmatch("laptop/2026-11-03/14-22-05")
    assert INTERACTION.fullmatch("laptop/2026-11-03/14-22-05-2")
    assert INTERACTION.fullmatch("laptop/2026-11-03/14-22-05-10")
    assert not INTERACTION.fullmatch("laptop/2026-11-03/14-22-05-1")
    assert not INTERACTION.fullmatch("laptop/2026-11-03/14-22-05-02")
    assert not INTERACTION.fullmatch("2026-11-03/14-22-05")
    assert not INTERACTION.fullmatch("laptop/2026-11-03")
    assert not INTERACTION.fullmatch("laptop/2026-11-03/14-22")

    assert ACTION_ID.fullmatch("2026-11-03/14-22-05")
    assert ACTION_ID.fullmatch("2026-11-03/14-22-05-3")
    assert not ACTION_ID.fullmatch("laptop/2026-11-03/14-22-05")

    assert OPERATION_REF.fullmatch("laptop/2026-11-03/14-22-05#op1")
    assert OPERATION_REF.fullmatch("laptop/2026-11-03/14-22-05-2#op2")
    assert OPERATION_REF.fullmatch("laptop/2026-11-03/14-22-05#op12")
    assert not OPERATION_REF.fullmatch("laptop/2026-11-03/14-22-05#op0")
    assert not OPERATION_REF.fullmatch("laptop/2026-11-03/14-22-05")

    assert ACTIVITY_ID.fullmatch("act-0042")
    assert ACTIVITY_ID.fullmatch("act-10000")
    assert not ACTIVITY_ID.fullmatch("act-042")
    assert STRIVING_ID.fullmatch("str-0003")
    assert not STRIVING_ID.fullmatch("act-0003")


def test_next_id_sequence(tmp_path: Path) -> None:
    assert issued(tmp_path, "activity") == 0
    assert next_id(tmp_path, "activity") == "act-0001"
    assert next_id(tmp_path, "activity") == "act-0002"
    assert next_id(tmp_path, "striving") == "str-0001"
    assert issued(tmp_path, "activity") == 2
    assert json.loads((tmp_path / ".next-ids").read_text()) == {"activity": 2, "striving": 1}
    assert [p.name for p in tmp_path.iterdir()] == [".next-ids"]


def test_next_id_beyond_9999(tmp_path: Path) -> None:
    (tmp_path / ".next-ids").write_text(json.dumps({"activity": 9999}))
    assert next_id(tmp_path, "activity") == "act-10000"
    assert next_id(tmp_path, "striving") == "str-0001"


def test_expected_path() -> None:
    interaction = "laptop/2026-11-03/14-22-05"
    observation = ObservationMeta(interaction=interaction, device="laptop", start=T, end=T)
    operations = OperationsMeta(
        interaction=interaction,
        device="laptop",
        start=T,
        end=T,
        operations=[Operation(id="op1", text="x", confidence=5)],
    )
    action = ActionMeta(
        id="2026-11-03/14-22-05", summary="s", start=T, end=T, operations=[], confidence=5
    )
    activity = ActivityMeta(
        id="act-0042", title="t", confidence=5, status="tentative", first_seen=T, last_seen=T
    )
    striving = StrivingMeta(
        id="str-0003",
        title="t",
        confidence=5,
        needs=["growth"],
        orientation="approach",
        aspiration_vs_obligation="ideal",
        autonomy="autonomous",
    )
    assert expected_path("observation", observation) == "observations/laptop/2026-11-03/14-22-05.md"
    assert expected_path("operations", operations) == "operations/laptop/2026-11-03/14-22-05.md"
    assert expected_path("action", action) == "actions/2026-11-03/14-22-05.md"
    assert expected_path("activity", activity) == "activities/act-0042.md"
    assert expected_path("striving", striving) == "strivings/str-0003.md"
