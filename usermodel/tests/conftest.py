"""Shared fixtures: a small valid model in a git repository, and helpers to change and commit it."""

import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import BaseModel

from vitality_usermodel.formats import (
    ActionMeta,
    ActivityMeta,
    ObservationMeta,
    Operation,
    OperationsMeta,
    StrivingMeta,
    write_file,
)

EST = timezone(timedelta(hours=-5))
INTERACTION = "laptop/2026-11-03/14-22-05"
OBSERVATION = f"observations/{INTERACTION}.md"
OPERATIONS = f"operations/{INTERACTION}.md"
ACTION = "actions/2026-11-03/14-22-05.md"
ACTIVITY = "activities/act-0001.md"
STRIVING = "strivings/str-0001.md"


def at(hour: int, minute: int, second: int) -> datetime:
    """A time on 2026-11-03 at UTC-5."""
    return datetime(2026, 11, 3, hour, minute, second, tzinfo=EST)


def git(model_dir: Path, *args: str) -> str:
    """Run git in `model_dir` with a fixed identity; returns stdout, fails the test on error."""
    result = subprocess.run(
        [
            "git",
            "-c",
            "user.name=test",
            "-c",
            "user.email=test@example.com",
            "-C",
            str(model_dir),
            *args,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def commit(model_dir: Path, message: str = "change") -> None:
    """Stage everything and commit it."""
    git(model_dir, "add", "-A")
    git(model_dir, "commit", "-q", "--allow-empty", "-m", message)


def write(model_dir: Path, relative: str, meta: BaseModel, body: str = "") -> None:
    """Write a model file at `relative` inside `model_dir`."""
    write_file(model_dir / relative, meta, body)


def edit(model_dir: Path, relative: str, old: str, new: str) -> None:
    """Replace `old` (which must occur) by `new` in the file at `relative`."""
    path = model_dir / relative
    text = path.read_text(encoding="utf-8")
    assert old in text, f"{old!r} not in {relative}"
    path.write_text(text.replace(old, new), encoding="utf-8")


def set_issued(model_dir: Path, activity: int, striving: int) -> None:
    """Set the `.next-ids` counters."""
    (model_dir / ".next-ids").write_text(
        json.dumps({"activity": activity, "striving": striving}) + "\n"
    )


def observation(interaction: str, start: datetime, end: datetime) -> ObservationMeta:
    return ObservationMeta(
        interaction=interaction, device=interaction.split("/")[0], start=start, end=end
    )


def operations(interaction: str, start: datetime, end: datetime, count: int = 2) -> OperationsMeta:
    return OperationsMeta(
        interaction=interaction,
        device=interaction.split("/")[0],
        start=start,
        end=end,
        operations=[
            Operation(id=f"op{i}", text=f"Did thing {i}", confidence=7) for i in range(1, count + 1)
        ],
    )


def action(
    id: str, start: datetime, end: datetime, refs: list[str], part_of: list[str]
) -> ActionMeta:
    return ActionMeta(
        id=id,
        summary="Checking the portal",
        start=start,
        end=end,
        operations=refs,
        part_of=part_of,
        confidence=7,
    )


def activity(id: str, part_of: list[str] | None = None, locked: bool = False) -> ActivityMeta:
    return ActivityMeta(
        id=id,
        title="Applying to research internships",
        part_of=part_of or [],
        confidence=8,
        status="tentative",
        first_seen=at(9, 22, 5),
        last_seen=at(9, 22, 31),
        locked=locked,
    )


def striving(id: str) -> StrivingMeta:
    return StrivingMeta(
        id=id,
        title="Yanis is building a path into AI research",
        confidence=7,
        needs=["competence", "growth"],
        orientation="approach",
        aspiration_vs_obligation="ideal",
        autonomy="autonomous",
    )


@pytest.fixture
def model(tmp_path: Path) -> Path:
    """A committed valid model: one observation, one operations file with op1 and op2, one action
    citing both, one activity and one striving, plus `user-context.md` and `.next-ids`."""
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    git(model_dir, "init", "-q")
    write(
        model_dir,
        OBSERVATION,
        observation(INTERACTION, at(9, 22, 5), at(9, 22, 31)),
        "Firefox: co-op portal",
    )
    write(model_dir, OPERATIONS, operations(INTERACTION, at(9, 22, 5), at(9, 22, 31)))
    refs = [f"{INTERACTION}#op1", f"{INTERACTION}#op2"]
    write(
        model_dir,
        ACTION,
        action("2026-11-03/14-22-05", at(9, 22, 5), at(9, 22, 31), refs, ["act-0001"]),
    )
    write(model_dir, ACTIVITY, activity("act-0001", ["str-0001"]), "Applications.")
    write(model_dir, STRIVING, striving("str-0001"), "Reasoning.")
    (model_dir / "user-context.md").write_text("I am a student.\n")
    set_issued(model_dir, 1, 1)
    commit(model_dir, "fixture")
    return model_dir
