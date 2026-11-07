"""Where the daemon gets its images of the monitors from.

Contract: `FrameSource` and `ScreenFrame` (interface.py). `screen_numbers` /
`numbered_frames` (screens.py) number monitors by position, top-left first.

The implementation is `vitality_client.daemon.frames.portal.PortalFrameSource`, imported
separately because it loads GStreamer.
"""

from vitality_client.daemon.frames.interface import FrameSource, ScreenFrame
from vitality_client.daemon.frames.screens import numbered_frames, screen_numbers

__all__ = ["FrameSource", "ScreenFrame", "numbered_frames", "screen_numbers"]
