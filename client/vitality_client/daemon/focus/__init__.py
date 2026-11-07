"""Which monitor has the keyboard focus, when the desktop can tell: an optional hint.

Contract: `FocusHint` (interface.py). Implementations: `HyprlandFocus` (hyprland.py),
asking `hyprctl`, and `NoFocus`, the portable default that never knows.
`focus_hint(env)` picks one: Hyprland's when `HYPRLAND_INSTANCE_SIGNATURE` is set and
`hyprctl` is on PATH, else `NoFocus`.
"""

import shutil
from collections.abc import Mapping

from vitality_client.daemon.focus.hyprland import HyprlandFocus, match_focused
from vitality_client.daemon.focus.interface import FocusHint, NoFocus


def focus_hint(env: Mapping[str, str]) -> FocusHint:
    """The focus hint this desktop supports (`NoFocus` if none)."""
    if env.get("HYPRLAND_INSTANCE_SIGNATURE") and shutil.which("hyprctl"):
        return HyprlandFocus()
    return NoFocus()


__all__ = ["FocusHint", "HyprlandFocus", "NoFocus", "focus_hint", "match_focused"]
