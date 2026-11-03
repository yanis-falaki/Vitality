from pathlib import Path

import pytest
from conftest import (
    ACTION,
    ACTIVITY,
    INTERACTION,
    OPERATIONS,
    action,
    activity,
    at,
    commit,
    edit,
    git,
    observation,
    operations,
    set_issued,
    write,
)

from vitality_usermodel.check import Problem, check


def paths(problems: list[Problem]) -> list[str]:
    return [p.path for p in problems]


def test_valid_model_passes(model: Path) -> None:
    assert check(model) == []
    assert check(model, since="HEAD") == []


def test_valid_model_outside_git_passes(model: Path) -> None:
    import shutil

    shutil.rmtree(model / ".git")
    assert check(model) == []
    with pytest.raises(ValueError, match="not a git repository"):
        check(model, since="HEAD")


def test_broken_repository_raises(model: Path) -> None:
    # A corrupted .git/HEAD reads as "not a git repository" to git, so a bad config line is used.
    with (model / ".git/config").open("a") as f:
        f.write("[core\n")
    with pytest.raises(ValueError, match="git rev-parse: fatal: bad config"):
        check(model)


def test_unknown_since(model: Path) -> None:
    with pytest.raises(ValueError, match="nope"):
        check(model, since="nope")


# Rule 1


def test_bad_frontmatter(model: Path) -> None:
    edit(model, ACTIVITY, "status: tentative", "status: done")
    problems = check(model)
    assert paths(problems) == [ACTIVITY]
    assert problems[0].message.startswith("status: ")


def test_missing_frontmatter(model: Path) -> None:
    (model / "strivings/str-0002.md").write_text("no frontmatter\n")
    set_issued(model, 1, 2)
    assert check(model) == [Problem("strivings/str-0002.md", "missing frontmatter")]


# Rule 2


def test_id_path_mismatch(model: Path) -> None:
    write(model, "activities/act-0002.md", activity("act-0001", ["str-0001"]))
    set_issued(model, 2, 1)
    problems = check(model)
    assert (
        Problem("activities/act-0002.md", "id act-0001 belongs at activities/act-0001.md")
        in problems
    )


def test_bad_id_format(model: Path) -> None:
    write(model, "activities/act-2.md", activity("act-2", ["str-0001"]))
    problems = check(model)
    assert Problem("activities/act-2.md", "id: act-2 is not an activity ID") in problems


def test_bad_operation_id_format(model: Path) -> None:
    edit(model, OPERATIONS, "id: op2", "id: step2")
    problems = check(model)
    assert Problem(OPERATIONS, "operations.1.id: step2 is not an operation ID") in problems


def test_duplicate_id(model: Path) -> None:
    write(model, "activities/act-0002.md", activity("act-0001", ["str-0001"]))
    set_issued(model, 2, 1)
    problems = check(model)
    assert Problem(ACTIVITY, "duplicate id act-0001 (also in activities/act-0002.md)") in problems
    assert (
        Problem("activities/act-0002.md", f"duplicate id act-0001 (also in {ACTIVITY})") in problems
    )


def test_number_above_issued(model: Path) -> None:
    write(model, "activities/act-0003.md", activity("act-0003", ["str-0001"]))
    set_issued(model, 2, 1)
    assert check(model) == [
        Problem(
            "activities/act-0003.md", "id act-0003 is above the last issued activity number (2)"
        )
    ]


def test_bad_next_ids(model: Path) -> None:
    (model / ".next-ids").write_text("[]\n")
    problems = check(model)
    assert paths(problems) == [".next-ids"]
    assert "not a JSON object of integers" in problems[0].message


def test_existing_file_id_changed(model: Path) -> None:
    edit(model, ACTIVITY, "id: act-0001", "id: act-0002")
    set_issued(model, 2, 1)
    problems = check(model, since="HEAD")
    assert Problem(ACTIVITY, "id changed from act-0001 since HEAD") in problems


def test_deleted_id_allowed(model: Path) -> None:
    write(model, "activities/act-0002.md", activity("act-0002", ["str-0001"]))
    set_issued(model, 2, 1)
    commit(model)
    (model / "activities/act-0002.md").unlink()
    assert check(model, since="HEAD") == []


# Rule 3


def test_missing_operation_ref(model: Path) -> None:
    edit(model, ACTION, "#op2", "#op3")
    problems = check(model)
    assert Problem(ACTION, f"operations: no operation {INTERACTION}#op3") in problems


def test_missing_operations_file(model: Path) -> None:
    edit(model, ACTION, f"{INTERACTION}#op2", "laptop/2026-11-04/10-00-00#op1")
    problems = check(model)
    assert (
        Problem(ACTION, "operations: no operations file for laptop/2026-11-04/10-00-00") in problems
    )


def test_bad_operation_ref(model: Path) -> None:
    edit(model, ACTION, f"{INTERACTION}#op2", "op2")
    problems = check(model)
    assert Problem(ACTION, "operations: op2 is not an operation reference") in problems


def test_part_of_wrong_kind(model: Path) -> None:
    edit(model, ACTION, "part_of: [act-0001]", "part_of: [str-0001]")
    problems = check(model)
    assert Problem(ACTION, "part_of: str-0001 is not an activity ID") in problems


def test_missing_striving(model: Path) -> None:
    edit(model, ACTIVITY, "part_of: [str-0001]", "part_of: [str-0001]\nhinders: [str-0009]")
    edit(model, ACTIVITY, "hinders: []\n", "")
    problems = check(model)
    assert Problem(ACTIVITY, "hinders: no striving str-0009") in problems


# Rule 4


def test_operation_listed_twice(model: Path) -> None:
    edit(model, ACTION, "#op2", "#op1")
    problems = check(model)
    assert (
        problems.count(Problem(ACTION, f"operations: {INTERACTION}#op1 is listed more than once"))
        == 1
    )
    assert Problem(OPERATIONS, "op2 belongs to no action") in problems


def test_operation_in_two_actions(model: Path) -> None:
    write(
        model,
        "actions/2026-11-03/14-22-06.md",
        action(
            "2026-11-03/14-22-06", at(9, 22, 5), at(9, 22, 31), [f"{INTERACTION}#op2"], ["act-0001"]
        ),
    )
    problems = check(model)
    assert (
        Problem(ACTION, f"operations: {INTERACTION}#op2 is also in actions/2026-11-03/14-22-06.md")
        in problems
    )
    assert (
        Problem(
            "actions/2026-11-03/14-22-06.md", f"operations: {INTERACTION}#op2 is also in {ACTION}"
        )
        in problems
    )


def _new_evidence(model: Path) -> str:
    interaction = "laptop/2026-11-03/14-30-00"
    write(
        model,
        f"observations/{interaction}.md",
        observation(interaction, at(9, 30, 0), at(9, 30, 10)),
        "x",
    )
    write(
        model,
        f"operations/{interaction}.md",
        operations(interaction, at(9, 30, 0), at(9, 30, 10), count=1),
    )
    return interaction


def test_staged_operation_unfiled(model: Path) -> None:
    interaction = _new_evidence(model)
    git(model, "add", "operations")
    assert check(model) == [Problem(f"operations/{interaction}.md", "op1 belongs to no action")]


def test_unstaged_operation_ignored(model: Path) -> None:
    _new_evidence(model)
    assert check(model) == []


# Rule 5


def test_action_times_outside_operations(model: Path) -> None:
    edit(model, ACTION, "end: '2026-11-03T09:22:31-05:00'", "end: '2026-11-03T09:25:00-05:00'")
    assert check(model) == [
        Problem(
            ACTION,
            "end: 2026-11-03T09:25:00-05:00 is after its operations end (2026-11-03T09:22:31-05:00)",
        )
    ]


def test_action_start_before_operations(model: Path) -> None:
    edit(model, ACTION, "start: '2026-11-03T09:22:05-05:00'", "start: '2026-11-03T14:00:00+00:00'")
    assert check(model) == [
        Problem(
            ACTION,
            "start: 2026-11-03T14:00:00+00:00 is before its operations start (2026-11-03T09:22:05-05:00)",
        )
    ]


# Rule 6


def test_locked_file_changed(model: Path) -> None:
    write(model, ACTIVITY, activity("act-0001", ["str-0001"], locked=True))
    commit(model)
    edit(model, ACTIVITY, "title: Applying", "title: Seeking")
    assert check(model, since="HEAD") == [Problem(ACTIVITY, "locked file changed since HEAD")]


def test_locked_file_unlocked_or_deleted(model: Path) -> None:
    write(model, ACTIVITY, activity("act-0001", ["str-0001"], locked=True))
    commit(model)
    edit(model, ACTIVITY, "locked: true", "locked: false")
    assert check(model, since="HEAD") == [Problem(ACTIVITY, "locked file changed since HEAD")]
    git(model, "rm", "-q", "-f", ACTIVITY)
    edit(model, ACTION, "part_of: [act-0001]", "part_of: []")
    assert check(model, since="HEAD") == [Problem(ACTIVITY, "locked file deleted since HEAD")]


def test_evidence_changed(model: Path) -> None:
    edit(model, OPERATIONS, "Did thing 2", "Did another thing")
    assert check(model, since="HEAD") == [Problem(OPERATIONS, "evidence changed since HEAD")]


def test_user_context_changed(model: Path) -> None:
    (model / "user-context.md").write_text("I am a researcher.\n")
    assert check(model, since="HEAD") == [
        Problem("user-context.md", "user-context.md changed since HEAD")
    ]


def test_other_file_changed(model: Path) -> None:
    (model / "notes.txt").write_text("hi\n")
    assert check(model, since="HEAD") == [
        Problem("notes.txt", "outside the allowed paths, added since HEAD")
    ]


def test_added_evidence_allowed(model: Path) -> None:
    interaction = _new_evidence(model)
    write(
        model,
        "actions/2026-11-03/14-30-00.md",
        action(
            "2026-11-03/14-30-00", at(9, 30, 0), at(9, 30, 10), [f"{interaction}#op1"], ["act-0001"]
        ),
    )
    (model / "modeler-notes.md").write_text("notes\n")
    assert check(model, since="HEAD") == []
    git(model, "add", "-A")
    assert check(model, since="HEAD") == []
