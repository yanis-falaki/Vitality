"""The contract for getting whole-desktop images."""

from typing import Protocol

from PIL import Image


class FrameSource(Protocol):
    """Keeps the newest image of the whole desktop available."""

    available: bool
    """False while no frames can be produced (not started, permission denied, stream lost)."""

    async def start(self) -> None:
        """Begin producing frames. May wait for the user to approve screen sharing."""
        ...

    def latest_frame(self) -> Image.Image | None:
        """The newest whole-desktop image, or None if none has arrived yet."""
        ...

    async def close(self) -> None:
        """Stop producing frames and release everything."""
        ...
