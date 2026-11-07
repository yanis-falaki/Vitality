"""Numbering monitors by where they sit: `screen_numbers` and `numbered_frames`.

One monitor has no number (the machine has just "the screen"). Several are numbered from 1,
top-left first: ordered by position (y, then x), so a row reads left to right and a stack
top to bottom, and the same layout always gets the same numbers. Equal positions keep the
order they came in (session order), e.g. when the portal does not report positions.
"""

from collections.abc import Sequence

from PIL import Image

from vitality_client.daemon.frames.interface import ScreenFrame


def _key(position: tuple[int, int]) -> tuple[int, int]:
    return position[1], position[0]


def screen_numbers(positions: Sequence[tuple[int, int]]) -> list[int | None]:
    """The number of each monitor at `positions` ((x, y)), in the same order as `positions`."""
    if len(positions) == 1:
        return [None]
    order = sorted(range(len(positions)), key=lambda i: _key(positions[i]))  # stable
    numbers: list[int | None] = [None] * len(positions)
    for rank, index in enumerate(order, start=1):
        numbers[index] = rank
    return numbers


def numbered_frames(
    frames: Sequence[tuple[Image.Image, tuple[int, int], tuple[int, int] | None]],
) -> list[ScreenFrame]:
    """(image, logical position, logical size or None), in session order → `ScreenFrame`s,
    numbered and in number order."""
    numbers = screen_numbers([position for _, position, _ in frames])
    screens = [ScreenFrame(number, image, position, size or image.size)
               for number, (image, position, size) in zip(numbers, frames)]
    return sorted(screens, key=lambda s: _key(s.position))  # stable, like screen_numbers
