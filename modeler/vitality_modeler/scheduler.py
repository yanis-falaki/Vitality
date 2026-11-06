"""The modeler as a service: when to run a session (modeler service spec, section 4).

`Scheduler.run` checks the model folder every `CHECK_SECONDS`. A session is due when
`TRIGGER_OPERATIONS` or more operations are waiting (in untracked operations files), or when
anything is waiting and no new operations file has appeared for `IDLE_SECONDS`. After a commit it
checks again at once; after `MAX_FAILED_IN_A_ROW` sessions without a commit it pauses (returns 1)
until restarted; a `limited` session makes it wait `LIMIT_WAIT_SECONDS`. While the owner has
uncommitted edits in the model folder it waits. What it is doing is kept in `<state>/status.json`;
`<state>/scheduler.lock` keeps a second scheduler from running on the same state.
"""

import fcntl
import json
import sys
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import IO, Any, Protocol

from vitality_usermodel.formats import write_text_atomic

from vitality_modeler import repo
from vitality_modeler.config import ModelerConfig
from vitality_modeler.playbook import PLAYBOOK, PLAYBOOK_VERSION
from vitality_modeler.session import run_session

CHECK_SECONDS = 30
TRIGGER_OPERATIONS = 20
IDLE_SECONDS = 300
LIMIT_WAIT_SECONDS = 3600
MAX_FAILED_IN_A_ROW = 3


class _Outcome(Protocol):
    @property
    def outcome(self) -> str: ...


def _stderr(line: str) -> None:
    print(f"vitality-modeler: {line}", file=sys.stderr, flush=True)


class Scheduler:
    def __init__(self, model: Path, state: Path, config: ModelerConfig, *, captures: str | None = "/captures",
                 session: Callable[..., _Outcome | None] = run_session,
                 clock: Callable[[], float] = time.monotonic,
                 now: Callable[[], datetime] = lambda: datetime.now().astimezone(),
                 sleep: Callable[[float], Any] = time.sleep, log: Callable[[str], None] = _stderr):
        self._model, self._state, self._config, self._captures = model, state, config, captures
        self._session, self._clock, self._now, self._sleep, self._log = session, clock, now, sleep, log
        self._seen: set[str] = set()         # waiting files at the last check
        self._last_new = clock()             # when a new waiting file last appeared
        self._failed = 0                     # sessions in a row without a commit
        self._limit_until: float | None = None
        self._edits_logged = False
        self._status: str | None = None
        self._lock: IO[str] | None = None
        self.paused = False

    def lock(self) -> None:
        """Take `<state>/scheduler.lock` for this scheduler's life. Raises RuntimeError if another holds it."""
        handle = open(self._state / "scheduler.lock", "w")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            raise RuntimeError(f"another modeler is running on {self._state}") from None
        self._lock = handle

    def run(self) -> int:
        """Run until paused (returns 1) or interrupted (KeyboardInterrupt; returns 0)."""
        self.lock()
        self._write("idle", force=True)
        try:
            while True:
                wait = self.step()
                if self.paused:
                    return 1
                if wait:
                    self._sleep(wait)
        except KeyboardInterrupt:
            self._write("idle", force=True)
            self._log("stopped")
            return 0

    def step(self) -> float:
        """One check, and a session if one is due; the seconds to wait before the next check."""
        if self._limit_until is not None:
            if self._clock() < self._limit_until:
                return min(CHECK_SECONDS, self._limit_until - self._clock())
            self._limit_until = None
        if repo.foreign_changes(self._model):
            if not self._edits_logged:
                self._log("waiting: the model folder has uncommitted edits; commit or discard them to continue")
                self._edits_logged = True
            self._write("waiting-edits")
            return CHECK_SECONDS
        self._edits_logged = False

        waiting = repo.waiting(self._model)
        if set(waiting) - self._seen:
            self._last_new = self._clock()
        self._seen = set(waiting)
        total = sum(waiting.values())
        if total >= TRIGGER_OPERATIONS:
            trigger = "20 operations"
        elif total and self._clock() - self._last_new >= IDLE_SECONDS:
            trigger = "idle"
        else:
            self._write("idle")
            return CHECK_SECONDS

        self._write("running")
        try:
            record = self._session(self._model, self._state, self._config, PLAYBOOK, PLAYBOOK_VERSION,
                                   trigger=trigger, captures=self._captures)
            outcome = record.outcome if record is not None else None
        except KeyboardInterrupt:
            raise
        except Exception as error:
            self._log(f"session crashed: {type(error).__name__}: {error}")
            outcome = "failed"
        if outcome is None:
            self._write("idle")
            return CHECK_SECONDS
        if outcome == "committed":
            self._failed = 0
            return 0
        if outcome == "limited":
            self._limit_until = self._clock() + LIMIT_WAIT_SECONDS
            until = self._now() + timedelta(seconds=LIMIT_WAIT_SECONDS)
            self._log(f"usage limit reached; waiting until {until:%H:%M}")
            self._write("waiting-limit", until=until)
            return min(CHECK_SECONDS, LIMIT_WAIT_SECONDS)
        self._failed += 1
        if self._failed >= MAX_FAILED_IN_A_ROW:
            self._log(f"paused: {MAX_FAILED_IN_A_ROW} sessions in a row without a commit; "
                      "see sessions.jsonl, then restart the modeler")
            self._write("paused")
            self.paused = True
            return 0
        self._write("idle")
        return CHECK_SECONDS

    def _write(self, state: str, *, until: datetime | None = None, force: bool = False) -> None:
        # Record the scheduler's state in status.json when it changes.
        if state == self._status and not force:
            return
        self._status = state
        record = {"state": state, "since": self._now().isoformat(timespec="seconds"),
                  "until": until.isoformat(timespec="seconds") if until is not None else None}
        write_text_atomic(self._state / "status.json", json.dumps(record) + "\n")
