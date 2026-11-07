"""The contract for getting the newest image of each monitor."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from PIL import Image


@dataclass(frozen=True)
class ScreenFrame:
    """The newest image of one monitor. Position and size are logical (compositor) pixels;
    the image is in the monitor's own physical pixels."""

    number: int | None  # 1, 2, ... top-left first (see screens.py); None when there is one screen
    image: Image.Image
    position: tuple[int, int]
    size: tuple[int, int]  # the image's size when the portal did not report one


class FrameSource(Protocol):
    """Keeps the newest image of every captured monitor available."""

    available: bool
    """False while no frames can be produced (not started, permission denied, stream lost)."""

    @property
    def screens(self) -> int:
        """How many monitors are being captured (0 while unavailable)."""
        ...

    async def start(self, on_restore_token: Callable[[int, str], None]) -> None:
        """Begin producing frames. May wait for the user to approve screen sharing, once
        per screen-sharing session.

        When the system grants a screen-sharing permission token for session `i`,
        `on_restore_token(i, token)` is called so it can be saved and passed back on the
        next start (no dialog then).
        """
        ...

    def latest_frames(self) -> list[ScreenFrame] | None:
        """The newest image of each monitor, numbered and in number order, or None until
        every monitor has delivered one."""
        ...

    async def close(self) -> None:
        """Stop producing frames and release everything."""
        ...
