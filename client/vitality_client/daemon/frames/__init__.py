"""Where the daemon gets its whole-desktop images from.

Contract: `FrameSource` (interface.py). `composite` / `layout_frames` (compose.py)
combine several monitors into one image.

The implementation is `vitality_client.daemon.frames.portal.PortalFrameSource`, imported
separately because it loads GStreamer.
"""

from vitality_client.daemon.frames.compose import composite, layout_frames
from vitality_client.daemon.frames.interface import FrameSource

__all__ = ["FrameSource", "composite", "layout_frames"]
