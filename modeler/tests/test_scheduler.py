import json
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from conftest import add_evidence
from vitality_modeler import repo
from vitality_modeler.config import ModelerConfig
from vitality_modeler.scheduler import IDLE_SECONDS, LIMIT_WAIT_SECONDS, Scheduler

T0 = datetime(2026, 11, 6, 14, 0, tzinfo=timezone(timedelta(hours=-5)))


def git(model: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(model), *args], capture_output=True, text=True, check=True).stdout


@dataclass
class Record:
    outcome: str


class Clock:
    """A fake monotonic clock that the fake sleep advances."""

    def __init__(self) -> None:
        self.t = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.t

    def now(self) -> datetime:
        return T0 + timedelta(seconds=self.t)

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds


class Session:
    """Plays the given outcomes in order (then the last again); `committed` commits all waiting evidence."""

    def __init__(self, *outcomes: str, during: Any = None) -> None:
        self.outcomes = list(outcomes) or ["committed"]
        self.calls: list[dict[str, Any]] = []
        self.during = during

    def __call__(self, model: Path, state: Path, config: ModelerConfig, playbook: str, version: str,
                 **kwargs: Any) -> Record:
        self.calls.append(kwargs)
        batch = list(repo.waiting(model))  # like the real harness, commit only what was offered
        if self.during is not None:
            self.during()
        outcome = self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]
        if outcome == "crash":
            raise RuntimeError("boom")
        if outcome == "committed":
            git(model, "add", "--", *batch, *(p.replace("operations/", "observations/", 1) for p in batch))
            git(model, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "modeled")
        return Record(outcome)


@pytest.fixture
def state(tmp_path: Path) -> Path:
    folder = tmp_path / "state"
    folder.mkdir()
    return folder


def scheduler(model: Path, state: Path, session: Session, clock: Clock, logs: list[str] | None = None) -> Scheduler:
    return Scheduler(model, state, ModelerConfig(), session=session, clock=clock, now=clock.now,
                     sleep=clock.sleep, log=(logs.append if logs is not None else lambda line: None))


def evidence(model: Path, n: int, ops: int = 1, hour: int = 9) -> None:
    for i in range(n):
        add_evidence(model, f"laptop/2026-11-06/{hour + 5:02d}-{i:02d}-00", f"2026-11-06T{hour:02d}:{i:02d}:00-05:00", ops)


def status(state: Path) -> dict[str, Any]:
    return json.loads((state / "status.json").read_text())


def test_waiting_counts_untracked_operations(model: Path):
    evidence(model, 2, ops=3)
    assert repo.waiting(model) == {"operations/laptop/2026-11-06/14-00-00.md": 3,
                                   "operations/laptop/2026-11-06/14-01-00.md": 3}


def test_waits_below_trigger(model: Path, state: Path):
    evidence(model, 10)
    session, clock = Session(), Clock()
    s = scheduler(model, state, session, clock)
    for _ in range(5):
        clock.sleep(s.step())
    assert session.calls == []
    assert status(state)["state"] == "idle"


def test_triggers_at_20(model: Path, state: Path):
    evidence(model, 4, ops=5)
    session, clock = Session(), Clock()
    scheduler(model, state, session, clock).step()
    assert [c["trigger"] for c in session.calls] == ["20 operations"]
    assert session.calls[0]["captures"] == "/captures"


def test_idle_trigger_after_5_minutes(model: Path, state: Path):
    evidence(model, 3)
    session, clock = Session(), Clock()
    s = scheduler(model, state, session, clock)
    while clock.t < IDLE_SECONDS - 30:
        clock.sleep(s.step())
    assert session.calls == []
    while not session.calls:
        clock.sleep(s.step())
    assert session.calls[0]["trigger"] == "idle"
    assert clock.t >= IDLE_SECONDS


def test_new_file_restarts_idle_timer(model: Path, state: Path):
    evidence(model, 3)
    session, clock = Session(), Clock()
    s = scheduler(model, state, session, clock)
    while clock.t < 240:
        clock.sleep(s.step())
    add_evidence(model, "laptop/2026-11-06/15-00-00", "2026-11-06T10:00:00-05:00", 1)
    while clock.t < 240 + IDLE_SECONDS - 30:
        clock.sleep(s.step())
    assert session.calls == []
    while not session.calls:
        clock.sleep(s.step())
    assert clock.t >= 240 + IDLE_SECONDS


def test_continues_after_commit(model: Path, state: Path):
    evidence(model, 4, ops=5)
    session, clock = Session(), Clock()
    s = scheduler(model, state, session, clock)
    assert s.step() == 0
    evidence(model, 4, ops=5, hour=11)
    assert s.step() == 0
    assert len(session.calls) == 2


def test_pauses_after_3_failures(model: Path, state: Path):
    evidence(model, 4, ops=5)
    session, clock = Session("failed", "reverted", "failed", "committed"), Clock()
    logs: list[str] = []
    assert scheduler(model, state, session, clock, logs).run() == 1
    assert len(session.calls) == 3
    assert status(state)["state"] == "paused"
    assert any("paused" in line for line in logs)


def test_session_exception_counts_as_failure(model: Path, state: Path):
    evidence(model, 4, ops=5)
    session, clock = Session("crash"), Clock()
    logs: list[str] = []
    assert scheduler(model, state, session, clock, logs).run() == 1
    assert len(session.calls) == 3
    assert any("RuntimeError: boom" in line for line in logs)


def test_limited_waits_an_hour(model: Path, state: Path):
    evidence(model, 4, ops=5)
    session, clock = Session("limited", "committed"), Clock()
    s = scheduler(model, state, session, clock)
    clock.sleep(s.step())
    assert status(state)["state"] == "waiting-limit"
    assert status(state)["until"] == (T0 + timedelta(seconds=LIMIT_WAIT_SECONDS)).isoformat(timespec="seconds")
    while clock.t < LIMIT_WAIT_SECONDS - 1:
        clock.sleep(s.step())
    assert len(session.calls) == 1
    while len(session.calls) < 2:
        clock.sleep(s.step())
    assert clock.t >= LIMIT_WAIT_SECONDS


def test_waits_while_owner_edits(model: Path, state: Path):
    evidence(model, 4, ops=5)
    (model / "user-context.md").write_text("edited\n")
    session, clock = Session(), Clock()
    logs: list[str] = []
    s = scheduler(model, state, session, clock, logs)
    for _ in range(3):
        clock.sleep(s.step())
    assert session.calls == []
    assert status(state)["state"] == "waiting-edits"
    assert sum("uncommitted" in line for line in logs) == 1
    git(model, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-am", "edit")
    s.step()
    assert len(session.calls) == 1


def test_lock_refuses_second(model: Path, state: Path):
    first = scheduler(model, state, Session(), Clock())
    first.lock()
    with pytest.raises(RuntimeError, match="another modeler"):
        scheduler(model, state, Session(), Clock()).lock()


def test_stale_status_overwritten(model: Path, state: Path):
    (state / "status.json").write_text(json.dumps({"state": "paused", "since": "x", "until": None}))
    clock = Clock()

    def stop(seconds: float) -> None:
        raise KeyboardInterrupt

    s = Scheduler(model, state, ModelerConfig(), session=Session(), clock=clock, now=clock.now, sleep=stop,
                  log=lambda line: None)
    assert s.run() == 0
    assert status(state)["state"] == "idle"


def test_evidence_during_session_waits(model: Path, state: Path):
    evidence(model, 4, ops=5)

    def arrive() -> None:
        add_evidence(model, "laptop/2026-11-06/18-00-00", "2026-11-06T13:00:00-05:00", 2)

    session, clock = Session(during=arrive), Clock()
    s = scheduler(model, state, session, clock)
    s.step()
    assert repo.waiting(model) == {"operations/laptop/2026-11-06/18-00-00.md": 2}


def test_keyboard_interrupt_writes_idle(model: Path, state: Path):
    evidence(model, 4, ops=5)

    def interrupted(*args: Any, **kwargs: Any) -> Record:
        raise KeyboardInterrupt

    clock = Clock()
    s = Scheduler(model, state, ModelerConfig(), session=interrupted, clock=clock, now=clock.now,
                  sleep=clock.sleep, log=lambda line: None)
    assert s.run() == 0
    assert status(state)["state"] == "idle"


def test_status_idle_after_a_failed_session(model: Path, state: Path):
    evidence(model, 4, ops=5)
    session, clock = Session("failed"), Clock()
    scheduler(model, state, session, clock).step()
    assert status(state)["state"] == "idle"


def test_recovers_a_killed_session_on_start(model: Path, state: Path):
    evidence(model, 4, ops=5)
    start = git(model, "rev-parse", "HEAD").strip()
    offered = repo.choose_batch(model, 500)
    assert offered is not None
    repo.stage(model, offered)
    (model / "actions" / "2026-11-06").mkdir(parents=True)
    (model / "actions" / "2026-11-06" / "14-00-00.md").write_text("half written")
    (state / "session.json").write_text(json.dumps({"start": start}))  # a session was killed mid-way
    clock = Clock()

    def stop(seconds: float) -> None:
        raise KeyboardInterrupt

    session = Session()
    s = Scheduler(model, state, ModelerConfig(), session=session, clock=clock, now=clock.now, sleep=stop,
                  log=lambda line: None)
    s.run()
    assert not (model / "actions" / "2026-11-06" / "14-00-00.md").exists()
    assert not (state / "session.json").exists()
    assert len(session.calls) == 1  # then it carried on normally


def test_owner_edit_racing_a_session_is_not_a_failure(model: Path, state: Path):
    evidence(model, 4, ops=5)

    def refuse(*args: Any, **kwargs: Any) -> Record:
        (model / "user-context.md").write_text("edited just now\n")  # the owner, a moment earlier
        raise RuntimeError("the model folder has changes other than new evidence: user-context.md")

    clock = Clock()
    s = Scheduler(model, state, ModelerConfig(), session=refuse, clock=clock, now=clock.now, sleep=clock.sleep,
                  log=lambda line: None)
    for _ in range(5):
        clock.sleep(s.step())
    assert not s.paused
    assert status(state)["state"] == "waiting-edits"
    assert (model / "user-context.md").read_text() == "edited just now\n"  # the owner's edit is kept


def test_logs_each_session_start_and_end(model: Path, state: Path):
    evidence(model, 4, ops=5)
    logs: list[str] = []
    scheduler(model, state, Session("committed"), Clock(), logs).step()
    assert logs[0] == "session starting: 20 operations waiting (trigger: 20 operations)"
    assert logs[1].startswith("session ") and "committed" in logs[1]
