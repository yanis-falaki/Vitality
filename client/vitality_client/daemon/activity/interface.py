"""The contract for knowing when the user is active."""

from collections.abc import Callable
from typing import Protocol


class ActivitySource(Protocol):
    """Reports when the user touches an input device — never what they typed."""

    available: bool
    """False when no input device could be read (e.g. missing permission)."""

    async def run(self, on_activity: Callable[[float], None]) -> None:
        """Until cancelled, call `on_activity(timestamp)` whenever input happens."""
        ...
