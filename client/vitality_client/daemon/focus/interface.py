"""The contract for telling which monitor has the keyboard focus."""

from collections.abc import Sequence
from typing import Protocol

from vitality_client.daemon.frames import ScreenFrame


class FocusHint(Protocol):
    """Names the focused monitor among the captured ones, when the desktop can tell."""

    def focused_screen(self, screens: Sequence[ScreenFrame]) -> int | None:
        """The `number` of the screen with the keyboard focus, or None when unknown.
        Never raises."""
        ...


class NoFocus(FocusHint):
    """The portable default: the focused screen is never known."""

    def focused_screen(self, screens: Sequence[ScreenFrame]) -> int | None:
        return None
