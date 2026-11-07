"""Which monitors to save at a shot moment: `screens_to_capture` — pure logic, no I/O.

When the client captures several monitors, saving every one at every shot would mostly
repeat an idle screen. So, per shot moment:

- the first shot of an interaction (and a manual capture) saves every screen;
- a later shot saves each screen whose content changed since that screen's own last saved
  image in this interaction, plus the focused screen whenever a focus hint names one;
- if nothing qualifies, that moment saves nothing.

One screen is always saved (exactly the behaviour from before several monitors).

"Changed" compares small grayscale thumbnails (`thumbnail`, 64×36: one thumbnail pixel
averages ~30×30 screen pixels at 1920×1080). A thumbnail pixel counts as changed when its
gray level moved by more than `PIXEL_DELTA` (of 255); a screen changed when more than
`CHANGED_PIXELS` of its 2304 thumbnail pixels did (~0.5%). A blinking text cursor or a
clock's digit moves one or two cells by a few levels, and the mouse pointer moving touches
at most ~8 cells: ignored. A scrolled page, a new chat message or a switched window
changes dozens to hundreds: saved. Typing a word or two on an unfocused screen is not
enough on its own; the focused screen is saved regardless when the hint is available.
"""

from collections.abc import Hashable, Mapping
from typing import TypeVar

from PIL import Image

THUMB_SIZE = (64, 36)
PIXEL_DELTA = 16
CHANGED_PIXELS = 12

Key = TypeVar("Key", bound=Hashable | None)  # a screen's number, or None for the only one


def thumbnail(image: Image.Image) -> bytes:
    """A 64×36 grayscale summary of `image` (one byte per pixel), for `changed`."""
    return image.resize(THUMB_SIZE, Image.Resampling.BOX).convert("L").tobytes()


def changed(before: bytes, after: bytes) -> bool:
    """Whether the screen behind two thumbnails changed visibly (see the module docstring)."""
    if len(before) != len(after):
        return True
    moved = sum(1 for a, b in zip(before, after) if abs(a - b) > PIXEL_DELTA)
    return moved > CHANGED_PIXELS


def screens_to_capture(
    current: Mapping[Key, bytes],
    last: Mapping[Key, bytes],
    focused: Key | None,
) -> list[Key]:
    """The screens to save now, in the order of `current`.

    `current` holds each screen's thumbnail now, by number; `last` the thumbnail of each
    screen's last saved image in this interaction (empty for the first shot or a manual
    capture, so every screen is saved); `focused` the screen the focus hint names, or None.
    A screen missing from `last` (none saved yet) is saved.
    """
    if len(current) == 1:
        return list(current)
    return [key for key, thumb in current.items()
            if key == focused or key not in last or changed(last[key], thumb)]
