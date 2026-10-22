"""The contract for the daemon's memory across restarts."""

from dataclasses import dataclass
from typing import Protocol


@dataclass
class State:
    """What survives a restart."""

    paused: bool = False
    paused_until: float | None = None  # epoch seconds; a timed pause
    restore_token: str | None = None   # the screen-sharing permission (single-use)


class StateStore(Protocol):
    """Loads and saves `State`."""

    def load(self) -> State:
        """The saved state; a fresh `State()` if none, paused if the saved copy is unreadable."""
        ...

    def save(self, state: State) -> None:
        """Save atomically."""
        ...
