"""The contract for knowing when the user is active."""

from typing import Protocol


class ActivitySource(Protocol):
    """Calls the callback it was built with — `on_activity(timestamp)` — whenever the user
    touches an input device. Reports only that input happened and when, never what it was."""

    available: bool
    """False when no input device could be read (e.g. missing permission)."""

    async def run(self) -> None:
        """Watch for input until cancelled."""
        ...
