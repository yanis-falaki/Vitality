"""The contract for getting whole-desktop images."""

from collections.abc import Callable
from typing import Protocol

from PIL import Image


class FrameSource(Protocol):
    """Keeps the newest image of the whole desktop available."""

    available: bool
    """False while no frames can be produced (not started, permission denied, stream lost)."""

    async def start(self, on_restore_token: Callable[[str], None]) -> None:
        """Begin producing frames. May wait for the user to approve screen sharing.

        When the system grants a screen-sharing permission token, `on_restore_token(token)`
        is called so it can be saved and passed back on the next start (no dialog then).
        """
        ...

    def latest_frame(self) -> Image.Image | None:
        """The newest whole-desktop image, or None if none has arrived yet."""
        ...

    async def close(self) -> None:
        """Stop producing frames and release everything."""
        ...
