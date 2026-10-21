"""Frame-source interface and multi-monitor compositing."""

from typing import Protocol

from PIL import Image


class FrameSource(Protocol):
    available: bool

    async def start(self) -> None: ...

    def latest_frame(self) -> Image.Image | None: ...

    async def close(self) -> None: ...


def composite(frames: list[tuple[Image.Image, tuple[int, int]]]) -> Image.Image:
    """Place each frame at its (x, y) on a black canvas sized to the bounding box.

    Positions may be negative; everything is shifted so the minimum becomes 0.
    A single frame is returned unchanged (same object).
    """
    if len(frames) == 1:
        return frames[0][0]
    min_x = min(x for _, (x, _) in frames)
    min_y = min(y for _, (_, y) in frames)
    max_x = max(x + img.width for img, (x, _) in frames)
    max_y = max(y + img.height for img, (_, y) in frames)
    canvas = Image.new("RGB", (max_x - min_x, max_y - min_y), (0, 0, 0))
    for img, (x, y) in frames:
        canvas.paste(img, (x - min_x, y - min_y))
    return canvas
