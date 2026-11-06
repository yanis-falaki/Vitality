import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from conftest import add_evidence
from vitality_modeler.main import main
from vitality_modeler.playbook import PLAYBOOK
from vitality_modeler.status import status_line

EST = timezone(timedelta(hours=-5))
NOW = datetime(2026, 11, 6, 15, 0, tzinfo=EST)


@pytest.fixture
def state(tmp_path: Path) -> Path:
    folder = tmp_path / "state"
    folder.mkdir()
    return folder


def write_status(state: Path, name: str, until: str | None = None) -> None:
    (state / "status.json").write_text(json.dumps({"state": name, "since": "2026-11-06T14:00:00-05:00",
                                                   "until": until}))


def session_line(state: Path, outcome: str = "committed") -> None:
    changes = {"actions": 9, "activities_created": 1, "activities_revised": 1, "activities_merged": 0,
               "strivings_created": 0, "strivings_revised": 0}
    with open(state / "sessions.jsonl", "a") as f:
        f.write(json.dumps({"started": "2026-11-06T19:32:00Z", "outcome": outcome, "changes": changes}) + "\n")


def test_no_sessions_yet(model: Path, state: Path):
    add_evidence(model, "laptop/2026-11-06/14-00-00", "2026-11-06T09:00:00-05:00", 3)
    write_status(state, "idle")
    assert status_line(model, state, NOW) == "modeler: 3 operations waiting · no sessions yet · idle"


def test_last_session_in_local_time(model: Path, state: Path):
    session_line(state)
    write_status(state, "running")
    assert status_line(model, state, NOW) == \
        "modeler: 0 operations waiting · last session 14:32 committed (9 actions, 2 activities) · running"


@pytest.mark.parametrize(("name", "until", "words"), [
    ("paused", None, "paused (3 failed)"),
    ("waiting-limit", "2026-11-06T15:40:00-05:00", "waiting (usage limit until 15:40)"),
    ("waiting-edits", None, "waiting (uncommitted edits in the model folder)"),
])
def test_states(model: Path, state: Path, name: str, until: str | None, words: str):
    write_status(state, name, until)
    assert status_line(model, state, NOW).endswith(" · " + words)


def test_not_running_without_or_with_unreadable_status(model: Path, state: Path):
    assert status_line(model, state, NOW).endswith(" · not running")
    (state / "status.json").write_text("{nope")
    (state / "sessions.jsonl").write_text("garbage\n")
    line = status_line(model, state, NOW)
    assert line.endswith(" · not running") and "last session" not in line


def test_status_command(model: Path, state: Path, capsys: pytest.CaptureFixture[str]):
    write_status(state, "idle")
    assert main(["status", "--model", str(model), "--state", str(state)]) == 0
    assert capsys.readouterr().out.startswith("modeler: 0 operations waiting")
    assert main(["status", "--model", str(state / "missing"), "--state", str(state)]) == 1
    assert "cannot read" in capsys.readouterr().err


def test_playbook_small_batches():
    assert "under about 100 operations" in " ".join(PLAYBOOK.split())
