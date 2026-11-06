import json
from typing import Any
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


@pytest.fixture
def held(state: Path):
    """The scheduler's lock, held as a running service holds it."""
    import fcntl
    with open(state / "scheduler.lock", "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def test_no_sessions_yet(model: Path, state: Path, held: None):
    add_evidence(model, "laptop/2026-11-06/14-00-00", "2026-11-06T09:00:00-05:00", 3)
    write_status(state, "idle")
    assert status_line(model, state, NOW) == "modeler: 3 operations waiting · no sessions yet · idle"


def test_last_session_in_local_time(model: Path, state: Path, held: None):
    session_line(state)
    write_status(state, "running")
    assert status_line(model, state, NOW) == \
        "modeler: 0 operations waiting · last session 14:32 committed (9 actions, 2 activities) · running"


@pytest.mark.parametrize(("name", "until", "words"), [
    ("paused", None, "paused (3 failed)"),
    ("waiting-limit", "2026-11-06T15:40:00-05:00", "waiting (usage limit until 15:40)"),
    ("waiting-edits", None, "waiting (uncommitted edits in the model folder)"),
])
def test_states(model: Path, state: Path, name: str, until: str | None, words: str, held: None):
    write_status(state, name, until)
    assert status_line(model, state, NOW).endswith(" · " + words)


def test_not_running_without_or_with_unreadable_status(model: Path, state: Path):
    assert status_line(model, state, NOW).endswith(" · not running")
    (state / "status.json").write_text("{nope")
    (state / "sessions.jsonl").write_text("garbage\n")
    line = status_line(model, state, NOW)
    assert line.endswith(" · not running") and "last session" not in line


def test_not_running_when_nobody_holds_the_lock(model: Path, state: Path):
    write_status(state, "idle")  # left behind by a stopped service
    assert status_line(model, state, NOW).endswith(" · not running")


def test_status_survives_a_malformed_evidence_file(model: Path, state: Path, capsys: pytest.CaptureFixture[str]):
    (model / "operations" / "laptop" / "2026-11-06").mkdir(parents=True)
    (model / "operations" / "laptop" / "2026-11-06" / "14-00-00.md").write_text("not frontmatter")
    assert main(["status", "--model", str(model), "--state", str(state)]) == 1
    assert "cannot read" in capsys.readouterr().err


def test_status_command(model: Path, state: Path, capsys: pytest.CaptureFixture[str], held: None):
    write_status(state, "idle")
    assert main(["status", "--model", str(model), "--state", str(state)]) == 0
    assert capsys.readouterr().out.startswith("modeler: 0 operations waiting")
    assert main(["status", "--model", str(state / "missing"), "--state", str(state)]) == 1
    assert "cannot read" in capsys.readouterr().err


def test_playbook_small_batches():
    assert "under about 100 operations" in " ".join(PLAYBOOK.split())


def test_paused_service_stays_up(model: Path, state: Path, monkeypatch: pytest.MonkeyPatch):
    from vitality_modeler import main as main_module

    class Paused:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def run(self) -> int:
            return 1

    slept: list[float] = []

    def sleep(seconds: float) -> None:
        slept.append(seconds)
        if len(slept) == 3:
            raise KeyboardInterrupt

    monkeypatch.setattr(main_module, "Scheduler", Paused)
    assert main_module._run(model, state, "/captures", sleep=sleep) == 1
    assert slept == [3600, 3600, 3600]  # it waited to be stopped instead of exiting


def test_paused_service_keeps_the_lock(model: Path, state: Path, monkeypatch: pytest.MonkeyPatch):
    import fcntl
    from vitality_modeler import main as main_module
    from vitality_modeler import scheduler as scheduler_module

    def failing(*args: object, **kwargs: object) -> None:
        raise RuntimeError("boom")

    add_evidence(model, "laptop/2026-11-06/14-00-00", "2026-11-06T09:00:00-05:00", 25)
    monkeypatch.setattr(scheduler_module, "run_session", failing)
    held_while_paused: list[bool] = []

    def sleep(seconds: float) -> None:
        if seconds == 3600:  # paused
            with open(state / "scheduler.lock") as other:
                try:
                    fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    held_while_paused.append(False)
                except BlockingIOError:
                    held_while_paused.append(True)
            raise KeyboardInterrupt

    class Quick(scheduler_module.Scheduler):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, session=failing, sleep=lambda s: None, log=lambda line: None, **kwargs)

    monkeypatch.setattr(main_module, "Scheduler", Quick)
    assert main_module._run(model, state, "/captures", sleep=sleep) == 1
    assert held_while_paused == [True]
