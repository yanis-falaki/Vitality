"""The daemon's memory across restarts: pause state and the portal restore token.

Saved atomically (temp file, fsync, rename). A missing file is a fresh start; an
existing but unreadable or corrupt one loads as paused, so a reboot never silently
resumes capture. Never logs file contents (they include the restore token).
"""

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class State:
    paused: bool = False
    paused_until: float | None = None
    restore_token: str | None = None


def _is_number(v: object) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


class StateFile:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def load(self) -> State:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return State()
        except (OSError, ValueError) as e:
            # Never log file contents: they may include the restore token.
            # An existing but unusable file must not silently resume capture.
            log.warning("unreadable state file %s (%s); starting paused",
                        self.path, type(e).__name__)
            return State(paused=True)
        paused = data.get("paused", False) if isinstance(data, dict) else None
        until = data.get("paused_until") if isinstance(data, dict) else None
        token = data.get("restore_token") if isinstance(data, dict) else None
        if (not isinstance(data, dict) or not isinstance(paused, bool)
                or not (until is None or _is_number(until))
                or not (token is None or isinstance(token, str))):
            log.warning("corrupt state file %s; starting paused", self.path)
            return State(paused=True)
        return State(paused, None if until is None else float(until), token)

    def save(self, state: State) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=self.path.name + ".")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(asdict(state), f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
