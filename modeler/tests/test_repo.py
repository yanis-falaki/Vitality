import subprocess
from pathlib import Path

from conftest import add_evidence
from vitality_modeler import repo


def git(model: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(model), *args], capture_output=True, text=True, check=True).stdout


def test_init_commits_start(model: Path):
    assert git(model, "log", "--format=%s").splitlines() == ["model: start"]
    assert git(model, "ls-files").split() == ["modeler-notes.md", "user-context.md"]
    assert git(model, "log", "--format=%an").strip() == "Vitality modeler"


def test_foreign_changes(model: Path):
    add_evidence(model, "laptop/2026-11-03/09-00-00", "2026-11-03T09:00:00+00:00", 1)
    assert repo.foreign_changes(model) == []
    (model / "user-context.md").write_text("edited\n")
    (model / "notes.txt").write_text("x\n")
    assert sorted(repo.foreign_changes(model)) == ["notes.txt", "user-context.md"]


def test_choose_batch_oldest_whole_files(model: Path):
    for name, iso in [("a/2026-11-03/10-00-00", "2026-11-03T10:00:00+00:00"),
                      ("a/2026-11-03/09-00-00", "2026-11-03T09:00:00+00:00"),
                      ("a/2026-11-03/11-00-00", "2026-11-03T11:00:00+00:00")]:
        add_evidence(model, name, iso, 3)
    offered = repo.choose_batch(model, 6)
    assert offered is not None
    assert offered.operations == ["operations/a/2026-11-03/09-00-00.md", "operations/a/2026-11-03/10-00-00.md"]
    assert offered.observations == ["observations/a/2026-11-03/09-00-00.md", "observations/a/2026-11-03/10-00-00.md"]
    assert offered.count == 6


def test_choose_batch_large_first_file(model: Path):
    add_evidence(model, "a/2026-11-03/09-00-00", "2026-11-03T09:00:00+00:00", 7)
    offered = repo.choose_batch(model, 5)
    assert offered is not None
    assert offered.operations == ["operations/a/2026-11-03/09-00-00.md"]
    assert offered.count == 7


def test_choose_batch_none(model: Path):
    assert repo.choose_batch(model, 5) is None


def test_revert_restores_offered_and_removes_created(model: Path):
    add_evidence(model, "a/2026-11-03/09-00-00", "2026-11-03T09:00:00+00:00", 2)
    offered = repo.choose_batch(model, 10)
    assert offered is not None
    repo.stage(model, offered)
    start = repo.head(model)
    paths = offered.operations + offered.observations
    before = {p: (model / p).read_bytes() for p in paths}

    (model / offered.operations[0]).write_text("tampered\n")
    (model / "actions/2026-11-03").mkdir(parents=True)
    (model / "actions/2026-11-03/10-00-00.md").write_text("a\n")
    (model / ".next-ids").write_text("act-0002\n")
    (model / "modeler-notes.md").write_text("changed\n")
    (model / "scratch.md").write_text("s\n")
    (model / "notes").mkdir()
    (model / "notes/x.md").write_text("x\n")
    add_evidence(model, "a/2026-11-03/12-00-00", "2026-11-03T12:00:00+00:00", 1)  # arrived meanwhile

    repo.revert(model, start, offered)

    assert {p: (model / p).read_bytes() for p in paths} == before
    assert not (model / "actions/2026-11-03/10-00-00.md").exists()
    assert not (model / ".next-ids").exists()
    assert (model / "modeler-notes.md").read_text() == "notes\n"
    assert repo.head(model) == start
    assert not (model / "scratch.md").exists()
    assert not (model / "notes").exists()
    assert repo.foreign_changes(model) == []
    assert git(model, "ls-files", "--others", "--exclude-standard", "--", "operations").split() == [
        *offered.operations, "operations/a/2026-11-03/12-00-00.md"]


def test_commit_and_counts(model: Path):
    start = repo.head(model)
    (model / "actions/2026-11-03").mkdir(parents=True)
    (model / "actions/2026-11-03/10-00-00.md").write_text("a\n")
    (model / "activities").mkdir()
    (model / "activities/act-0001.md").write_text("b\n")
    (model / "modeler-notes.md").write_text("more\n")
    assert repo.change_counts(model, start) == {
        "actions": 1, "activities_created": 1, "activities_revised": 0, "activities_merged": 0,
        "strivings_created": 0, "strivings_revised": 0,
    }
    commit = repo.commit_all(model, "model: batch")
    assert commit == repo.head(model) != start
    assert git(model, "log", "-1", "--format=%s") == "model: batch\n"


def test_commit_leaves_unoffered_evidence_untracked(model: Path):
    add_evidence(model, "a/2026-11-03/09-00-00", "2026-11-03T09:00:00+00:00", 1)
    offered = repo.choose_batch(model, 10)
    assert offered is not None
    repo.stage(model, offered)
    start = repo.head(model)
    add_evidence(model, "a/2026-11-03/12-00-00", "2026-11-03T12:00:00+00:00", 1)
    (model / "actions/2026-11-03").mkdir(parents=True)
    (model / "actions/2026-11-03/10-00-00.md").write_text("a\n")
    (model / "modeler-notes.md").write_text("more\n")

    assert repo.change_counts(model, start)["actions"] == 1
    commit = repo.commit_all(model, "model: batch")

    committed = git(model, "show", "--name-only", "--format=", commit).split()
    assert committed == sorted([
        "actions/2026-11-03/10-00-00.md", "modeler-notes.md", *offered.operations, *offered.observations])
    assert git(model, "ls-files", "--others", "--exclude-standard").split() == [
        "observations/a/2026-11-03/12-00-00.md", "operations/a/2026-11-03/12-00-00.md"]


def test_tracked_lists_staged_and_committed(model: Path):
    add_evidence(model, "laptop/2026-11-03/14-00-00", "2026-11-03T09:00:00-05:00", 1)
    add_evidence(model, "laptop/2026-11-03/15-00-00", "2026-11-03T10:00:00-05:00", 1)
    assert repo.tracked(model, "operations") == []
    batch = repo.choose_batch(model, 1)
    assert batch is not None
    repo.stage(model, batch)
    assert repo.tracked(model, "operations") == ["operations/laptop/2026-11-03/14-00-00.md"]
