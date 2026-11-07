"""The daemon's memory across restarts: pause state and the portal restore tokens.

The contract (`State`, `StateStore`) comes first; the implementation (`StateFile`) follows.
The daemon owns the file; the CLI only reads it (`doctor`, while the daemon is stopped).

Saved atomically (temp file, fsync, rename). A missing file is a fresh start; an
existing but unreadable or corrupt one loads as paused, so a reboot never silently
resumes capture. Never logs file contents (they include the restore tokens).

There is one restore token per screen-sharing session (`restore_tokens`). A file from
before several sessions holds a single `restore_token`; it loads as session 1's token.
Saving writes `restore_tokens`, plus session 1's token as `restore_token`, so an older
daemon can still read it.
"""


import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


# == Contract =============================================================================

@dataclass
class State:
    """What survives a restart."""

    paused: bool = False
    paused_until: float | None = None  # epoch seconds; a timed pause
    # The screen-sharing permission of each portal session, in session order (single-use).
    restore_tokens: list[str | None] = field(default_factory=list)


class StateStore(Protocol):
    """Loads and saves `State`."""

    def load(self) -> State:
        """The saved state; a fresh `State()` if none, paused if the saved copy is unreadable."""
        ...

    def save(self, state: State) -> None:
        """Save atomically."""
        ...


# == Implementation =======================================================================

log = logging.getLogger(__name__)


def _is_number(v: object) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _tokens(data: dict) -> list[str | None] | None:
    """The saved restore tokens (`restore_tokens`, else the older single `restore_token`),
    or None if they are malformed."""
    if "restore_tokens" in data:
        tokens = data["restore_tokens"]
        if isinstance(tokens, list) and all(t is None or isinstance(t, str) for t in tokens):
            return tokens
        return None
    token = data.get("restore_token")
    if token is None:
        return []
    return [token] if isinstance(token, str) else None


class StateFile(StateStore):
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
        tokens = _tokens(data) if isinstance(data, dict) else None
        if (not isinstance(data, dict) or not isinstance(paused, bool)
                or not (until is None or _is_number(until)) or tokens is None):
            log.warning("corrupt state file %s; starting paused", self.path)
            return State(paused=True)
        return State(paused, None if until is None else float(until), tokens)

    def save(self, state: State) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=self.path.name + ".")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({
                    "paused": state.paused,
                    "paused_until": state.paused_until,
                    "restore_tokens": state.restore_tokens,
                    "restore_token": next(iter(state.restore_tokens), None),  # older daemons
                }, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
