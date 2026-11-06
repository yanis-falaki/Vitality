"""The modeler's state, read from its records folder and the model repo (as `vitality-modeler status` does).

`read_modeler(state, model, count)` gives the scheduler's state (`status.json` while a scheduler holds
`scheduler.lock`, else `not running`), the operations waiting (untracked operations files, listed with
git), and every session record of `sessions.jsonl` with `offered` dropped. Nothing is written; an
unreadable part reads as absent.
"""

import fcntl
import json
import subprocess
from collections.abc import Callable
from pathlib import Path

from vitality_web import schema


def read_modeler(state: Path, model: Path, count: Callable[[list[str]], dict[str, int]]) -> schema.Modeler:
    """The modeler's state. `count` maps operations files (relative to `model`) to how many operations each holds."""
    waiting = _waiting(model)
    return schema.Modeler(
        status=_status(state),
        waiting=None if waiting is None else sum(count(waiting).values()),
        sessions=_sessions(state))


def _status(state: Path) -> schema.SchedulerState:
    if not _locked(state / "scheduler.lock"):
        return schema.SchedulerState(state="not running")
    try:
        record = json.loads((state / "status.json").read_text(encoding="utf-8"))
        return schema.SchedulerState.model_validate(record)
    except (OSError, ValueError):
        return schema.SchedulerState(state="not running")


def _sessions(state: Path) -> list[schema.Session]:
    try:
        lines = (state / "sessions.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    sessions: list[schema.Session] = []
    for line in lines:
        try:
            record = json.loads(line)
            record.pop("offered", None)
            sessions.append(schema.Session.model_validate(record))
        except (ValueError, AttributeError):
            continue  # a torn or foreign line
    return sessions


def _waiting(model: Path) -> list[str] | None:
    # The untracked operations files (the modeler's `repo.waiting`), or None if git cannot list them.
    try:
        listed = subprocess.run(
            ["git", "-C", str(model), "ls-files", "--others", "--exclude-standard", "-z", "--", "operations"],
            capture_output=True, text=True, timeout=20, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return [path for path in listed.split("\0") if path.endswith(".md")]


def _locked(path: Path) -> bool:
    # True when a scheduler holds the lock (a shared lock cannot be taken).
    try:
        with open(path) as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            return False
    except OSError:
        return False
