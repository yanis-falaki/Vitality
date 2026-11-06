"""`vitality-modeler status`: what the modeler service is doing, in one line (modeler service spec, section 6).

    modeler: 2412 operations waiting · last session 14:32 committed (9 actions, 2 activities) · running
"""

import json
from datetime import datetime
from pathlib import Path

from vitality_modeler import repo

_STATES = {
    "idle": "idle",
    "running": "running",
    "paused": "paused (3 failed)",
    "waiting-edits": "waiting (uncommitted edits in the model folder)",
}


def status_line(model: Path, state: Path, now: datetime) -> str:
    """The line: operations waiting in `model`, the last session in `<state>/sessions.jsonl`, and the
    scheduler's state from `<state>/status.json` (`not running` when that is missing or unreadable).
    Times are shown in `now`'s time zone. Raises OSError (or RuntimeError from git) if `model` cannot be read.
    """
    waiting = sum(repo.waiting(model).values())
    return f"modeler: {waiting} operations waiting · {_last_session(state, now)} · {_state(state, now)}"


def _last_session(state: Path, now: datetime) -> str:
    try:
        lines = (state / "sessions.jsonl").read_text(encoding="utf-8").splitlines()
        record = json.loads(lines[-1])
        started = datetime.fromisoformat(record["started"].replace("Z", "+00:00")).astimezone(now.tzinfo)
        changes = record["changes"]
        activities = changes["activities_created"] + changes["activities_revised"] + changes["activities_merged"]
        return (f"last session {started:%H:%M} {record['outcome']} "
                f"({changes['actions']} actions, {activities} activities)")
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        return "no sessions yet"


def _state(state: Path, now: datetime) -> str:
    try:
        record = json.loads((state / "status.json").read_text(encoding="utf-8"))
        name = record["state"]
        if name == "waiting-limit":
            until = datetime.fromisoformat(record["until"]).astimezone(now.tzinfo)
            return f"waiting (usage limit until {until:%H:%M})"
        return _STATES[name]
    except (OSError, ValueError, KeyError, TypeError):
        return "not running"
