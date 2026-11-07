"""Implements `FocusHint` (see interface.py) on Hyprland.

`HyprlandFocus` runs `hyprctl monitors -j` and matches the monitor marked `focused` to a
captured screen by position (Hyprland's layout coordinates, like the portal's), and, if
that does not single one out (e.g. the portal reported no positions), by size. Anything
unexpected (no hyprctl, an error, odd JSON, no unique match) gives no hint.
`match_focused(monitors, screens)` is the matching, public for tests.
"""

import json
import logging
import subprocess
from collections.abc import Callable, Sequence

from vitality_client.daemon.focus.interface import FocusHint
from vitality_client.daemon.frames import ScreenFrame

log = logging.getLogger(__name__)

_TIMEOUT = 1.0


def _hyprctl() -> str:
    return subprocess.run(["hyprctl", "monitors", "-j"], capture_output=True, text=True,
                          timeout=_TIMEOUT, check=True).stdout


class HyprlandFocus(FocusHint):
    def __init__(self, monitors_json: Callable[[], str] = _hyprctl) -> None:
        self._monitors_json = monitors_json

    def focused_screen(self, screens: Sequence[ScreenFrame]) -> int | None:
        try:
            return match_focused(json.loads(self._monitors_json()), screens)
        except Exception as exc:
            log.debug("no focus hint from hyprctl: %s", str(exc) or type(exc).__name__)
            return None


def match_focused(monitors: object, screens: Sequence[ScreenFrame]) -> int | None:
    """The number of the screen showing the focused monitor of `hyprctl monitors -j`, or None.

    Raises on malformed monitor entries (the caller turns that into no hint).
    """
    if not isinstance(monitors, list):
        return None
    focused = [m for m in monitors if isinstance(m, dict) and m.get("focused") is True]
    if len(focused) != 1:
        return None
    monitor = focused[0]
    position = (int(monitor["x"]), int(monitor["y"]))
    physical = (int(monitor["width"]), int(monitor["height"]))
    if int(monitor.get("transform", 0)) % 2:  # rotated 90° or 270°
        physical = (physical[1], physical[0])
    scale = float(monitor.get("scale", 1)) or 1.0
    logical = (round(physical[0] / scale), round(physical[1] / scale))

    at_position = [s for s in screens if s.position == position]
    if len(at_position) == 1:
        return at_position[0].number
    sized = [s for s in (at_position or screens)
             if s.size in (logical, physical) or s.image.size == physical]
    return sized[0].number if len(sized) == 1 else None
