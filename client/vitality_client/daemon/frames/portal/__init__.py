"""Screen frames from xdg-desktop-portal ScreenCast (works on any Wayland desktop).

`PortalFrameSource(restore_token, on_restore_token)` implements `FrameSource`.
Inside: `session.py` talks to the portal over D-Bus (permission dialog, restore
token, PipeWire streams); `stream.py` reads those streams with GStreamer and keeps
the newest frame; `source.py` combines the two and retries with backoff.

Importing this package loads GStreamer, so import it only where frames are needed.
"""

from vitality_client.daemon.frames.portal.session import PortalError, PortalResponseError
from vitality_client.daemon.frames.portal.source import PortalFrameSource

__all__ = ["PortalError", "PortalFrameSource", "PortalResponseError"]
