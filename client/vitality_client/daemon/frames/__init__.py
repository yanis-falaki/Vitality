"""Where the daemon gets its images of the monitors from.

Contract: `FrameSource` and `ScreenFrame` (interface.py). `screen_names` / `named_frames`
(screens.py) name monitors by position: `left`, `right`, ...

The implementation is `vitality_client.daemon.frames.portal.PortalFrameSource`, imported
separately because it loads GStreamer.
"""

from vitality_client.daemon.frames.interface import FrameSource, ScreenFrame
from vitality_client.daemon.frames.screens import named_frames, screen_names

__all__ = ["FrameSource", "ScreenFrame", "named_frames", "screen_names"]
