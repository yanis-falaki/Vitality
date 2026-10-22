"""GStreamer pipelines reading the portal's PipeWire streams, keeping the newest frame of each.

`FrameStreams(max_fps).start(fd, streams)` builds one pipeline per monitor;
`latest_frame()` returns the whole desktop as one RGB image (or None until every
stream has delivered a frame); `failure()` reports a pipeline error or end of
stream; `stop()` tears everything down.

The appsink callback runs on a GStreamer thread and only stores a reference to the
newest sample under a lock. Conversion to RGB happens in `latest_frame()`, i.e. only
when a snapshot needs an image. Never logged: image contents.
"""

import logging
import threading

import gi

gi.require_version("Gst", "1.0")
gi.require_version("GstVideo", "1.0")
from gi.repository import Gst, GstVideo  # noqa: E402
from PIL import Image  # noqa: E402

from vitality_client.frames.base import composite, layout_frames  # noqa: E402
from vitality_client.frames.portal.session import Stream  # noqa: E402

log = logging.getLogger(__name__)

# Packed formats the stream may arrive in, and the Pillow raw mode that reads each as RGB.
# Alpha is read as padding: screen frames are opaque.
_RAW_MODES = {
    "BGRx": "BGRX", "RGBx": "RGBX", "xRGB": "XRGB", "xBGR": "XBGR",
    "BGRA": "BGRX", "RGBA": "RGBX", "ARGB": "XRGB", "ABGR": "XBGR",
    "BGR": "BGR", "RGB": "RGB",
}


class StreamError(Exception):
    pass


def raw_mode_for(video_format: str) -> str:
    return _RAW_MODES[video_format]


def pipeline_description(fd: int, node_id: int, max_fps: int) -> str:
    # The frame-rate cap is a caps field so pipewiresrc negotiates it with the
    # producer (a downstream videorate stalled on the compositor's variable-rate
    # stream). Frames stay in the producer's format; they are converted to RGB only
    # when a snapshot asks for one, not on every frame.
    formats = ",".join(_RAW_MODES)
    return (
        f"pipewiresrc fd={fd} path={node_id} keepalive-time=1000 ! "
        f"video/x-raw,format=(string){{{formats}}},max-framerate={max_fps}/1 ! "
        "appsink name=sink max-buffers=1 drop=true sync=false emit-signals=true"
    )


def rgb_image_from_bytes(data: bytes | memoryview, width: int, height: int, stride: int,
                         raw_mode: str = "RGB") -> Image.Image:
    """Packed rows of `stride` bytes (extra bytes are padding) in `raw_mode` -> RGB image."""
    return Image.frombytes("RGB", (width, height), data, "raw", raw_mode, stride)


def sample_to_image(sample) -> Image.Image:
    caps = sample.get_caps()
    info = GstVideo.VideoInfo.new_from_caps(caps)
    raw_mode = raw_mode_for(caps.get_structure(0).get_value("format"))
    buffer = sample.get_buffer()
    meta = GstVideo.buffer_get_video_meta(buffer)
    offset, stride = (meta.offset[0], meta.stride[0]) if meta else (info.offset[0], info.stride[0])
    ok, mapinfo = buffer.map(Gst.MapFlags.READ)
    if not ok:
        raise StreamError("could not map the frame buffer")
    try:
        # A memoryview slice: no copy before PIL copies the pixels into the image.
        with memoryview(mapinfo.data) as view, view[offset:] as data:
            return rgb_image_from_bytes(data, info.width, info.height, stride, raw_mode)
    finally:
        buffer.unmap(mapinfo)


class FrameStreams:
    def __init__(self, max_fps: int) -> None:
        self._max_fps = max_fps
        self._pipelines: list[Gst.Pipeline] = []
        # Shared with GStreamer threads.
        self._lock = threading.Lock()
        self._streams: list[Stream] = []
        self._samples: dict[int, Gst.Sample] = {}  # newest sample per stream, converted lazily

    def __len__(self) -> int:
        return len(self._pipelines)

    def start(self, fd: int, streams: list[Stream]) -> None:
        Gst.init(None)
        with self._lock:
            self._streams = list(streams)
            self._samples = {}
        for index, stream in enumerate(streams):
            pipeline = Gst.parse_launch(pipeline_description(fd, stream.node_id, self._max_fps))
            self._pipelines.append(pipeline)
            pipeline.get_by_name("sink").connect("new-sample", self._on_sample, index)
            if pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
                raise StreamError(f"could not start the pipeline for stream {index}")

    def latest_frame(self) -> Image.Image | None:
        with self._lock:
            if not self._streams or len(self._samples) < len(self._streams):
                return None
            samples = [(self._samples[i], s) for i, s in enumerate(self._streams)]
        try:
            frames = []
            for sample, stream in samples:
                image = sample_to_image(sample)
                frames.append((image, stream.position, stream.size or image.size))
        except Exception as exc:
            log.debug("could not convert the latest frame: %s", str(exc) or type(exc).__name__)
            return None
        return composite(layout_frames(frames))

    def failure(self) -> str | None:
        """A pipeline error or end of stream since the last call, or None."""
        for pipeline in self._pipelines:
            msg = pipeline.get_bus().timed_pop_filtered(
                0, Gst.MessageType.ERROR | Gst.MessageType.EOS
            )
            if msg is None:
                continue
            if msg.type == Gst.MessageType.ERROR:
                err, _debug = msg.parse_error()
                return f"pipeline error: {err.message}"
            return "stream ended"
        return None

    def stop(self) -> None:
        pipelines, self._pipelines = self._pipelines, []
        for pipeline in pipelines:
            pipeline.set_state(Gst.State.NULL)
        with self._lock:
            self._streams = []
            self._samples = {}

    def _on_sample(self, sink, index: int) -> Gst.FlowReturn:
        # GStreamer streaming thread: no asyncio here, and never log image data.
        sample = sink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.OK
        with self._lock:
            if index < len(self._streams):
                self._samples[index] = sample
        return Gst.FlowReturn.OK
