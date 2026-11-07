"""Naming monitors by where they sit: `screen_names` and `named_frames`.

One monitor has no name (the machine has just "the screen"). Two are `left` and `right`;
three `left`, `middle`, `right`; more `screen-1`, `screen-2`, ... from left to right.
Monitors are ordered by position (x, then y); equal positions keep the order they came
in (session order), e.g. when the portal does not report positions.
"""

from collections.abc import Sequence

from PIL import Image

from vitality_client.daemon.frames.interface import ScreenFrame

_NAMES = {1: [None], 2: ["left", "right"], 3: ["left", "middle", "right"]}


def screen_names(positions: Sequence[tuple[int, int]]) -> list[str | None]:
    """The name of each monitor at `positions`, in the same order as `positions`."""
    count = len(positions)
    labels = _NAMES.get(count) or [f"screen-{n}" for n in range(1, count + 1)]
    order = sorted(range(count), key=lambda i: positions[i])  # stable: ties keep their order
    names: list[str | None] = [None] * count
    for rank, index in enumerate(order):
        names[index] = labels[rank]
    return names


def named_frames(
    frames: Sequence[tuple[Image.Image, tuple[int, int], tuple[int, int] | None]],
) -> list[ScreenFrame]:
    """(image, logical position, logical size or None), in session order → `ScreenFrame`s,
    named and ordered left to right."""
    names = screen_names([position for _, position, _ in frames])
    screens = [ScreenFrame(name, image, position, size or image.size)
               for name, (image, position, size) in zip(names, frames)]
    return sorted(screens, key=lambda s: s.position)  # stable, like screen_names
