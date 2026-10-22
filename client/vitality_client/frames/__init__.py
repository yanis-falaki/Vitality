"""Where the daemon gets its whole-desktop images from.

`FrameSource` is the interface the daemon depends on: `await start()`,
`latest_frame() -> Image | None` (the newest whole-desktop image, None until one
has arrived), `available`, `await close()`. `composite` / `layout_frames` combine
several monitors into one image.

The implementation is `vitality_client.frames.portal.PortalFrameSource` (imported
separately, since it loads GStreamer).
"""

from vitality_client.frames.base import FrameSource, composite, layout_frames

__all__ = ["FrameSource", "composite", "layout_frames"]
