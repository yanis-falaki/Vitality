import re
from datetime import datetime
from pathlib import Path

import pytest

from vitality_lab.record import git_state, read_run_yaml, run_folder_name, write_run_yaml

COMMIT = "ab8ae6a1f" + "0" * 31


def test_clean_state(repo: Path):
    state = git_state(repo, ["observer"])
    assert state.dirty is False
    assert state.diff == ""
    assert re.fullmatch(r"[0-9a-f]{40}", state.commit)
    assert state.untracked == []


def test_edit_makes_dirty(repo: Path):
    (repo / "observer" / "core.py").write_text("x = 2\n")
    state = git_state(repo, ["observer"])
    assert state.dirty is True
    assert "+x = 2" in state.diff


def test_untracked_only_under_watched(repo: Path):
    (repo / "observer" / "new.py").write_text("")
    (repo / "notes").mkdir()
    (repo / "notes" / "x.md").write_text("")
    assert git_state(repo, ["observer"]).untracked == ["observer/new.py"]


def test_not_a_repo_raises(tmp_path: Path):
    with pytest.raises(RuntimeError):
        git_state(tmp_path, ["observer"])


def test_run_folder_name():
    started = datetime(2026, 11, 4, 15, 30, 2)
    assert run_folder_name(started, COMMIT, False, None) == "2026-11-04T15-30-02-ab8ae6a"
    assert run_folder_name(started, COMMIT, True, "richer-prompt") == "2026-11-04T15-30-02-ab8ae6a-dirty-richer-prompt"


@pytest.mark.parametrize("label", ["has space", "a/b", "-x", ""])
def test_bad_label(label: str):
    with pytest.raises(ValueError):
        run_folder_name(datetime(2026, 11, 4), COMMIT, False, label)


def test_run_yaml_round_trip(tmp_path: Path):
    record = {"stage": "observer", "finished": None, "settings": {"model": "m", "parallel": 4},
              "failures": {"laptop/a": "bad"}, "not_run": ["laptop/b"]}
    path = tmp_path / "run.yaml"
    write_run_yaml(path, record)
    assert read_run_yaml(path) == record
    assert list(read_run_yaml(path)) == list(record)
    assert "{" not in path.read_text()
