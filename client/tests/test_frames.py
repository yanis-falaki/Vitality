import asyncio
from collections.abc import Callable

from PIL import Image

from vitality_client.daemon.frames import numbered_frames, screen_numbers
from vitality_client.daemon.frames.portal.session import (
    PortalError,
    PortalResponseError,
    PortalSession,
    Stream,
    request_path,
)
from vitality_client.daemon.frames.portal.source import PortalFrameSource, backoff_delay, should_retry
from vitality_client.daemon.frames.portal.stream import (
    pipeline_description,
    raw_mode_for,
    rgb_image_from_bytes,
)


def test_screen_numbers_top_left_first():
    assert screen_numbers([(0, 0)]) == [None]                                 # as before
    assert screen_numbers([(1920, 0), (0, 0)]) == [2, 1]                       # side by side
    assert screen_numbers([(0, 1080), (0, 0)]) == [2, 1]                       # stacked
    assert screen_numbers([(0, 0), (-1280, 200), (1920, 0), (0, -1080)]) == [2, 4, 3, 1]


def test_screen_numbers_equal_positions_keep_session_order():
    # e.g. a portal that reports no positions: the first session's monitor is screen 1.
    assert screen_numbers([(0, 0), (0, 0)]) == [1, 2]


def test_numbered_frames_in_number_order_with_logical_size():
    a = Image.new("RGB", (40, 20)); b = Image.new("RGB", (20, 10))
    first, second = numbered_frames([(a, (20, 0), (20, 10)), (b, (0, 0), None)])
    assert (first.number, first.image, first.size) == (1, b, (20, 10))      # size from the image
    assert (second.number, second.image, second.size, second.position) == (2, a, (20, 10), (20, 0))


def test_rgb_image_tight_rows():
    data = bytes([255, 0, 0, 0, 255, 0, 0, 0, 255, 9, 9, 9])  # 2x2, stride 6
    img = rgb_image_from_bytes(data, 2, 2, 6)
    assert img.size == (2, 2) and img.mode == "RGB"
    assert [img.getpixel(p) for p in [(0, 0), (1, 0), (0, 1), (1, 1)]] == [
        (255, 0, 0), (0, 255, 0), (0, 0, 255), (9, 9, 9)]


def test_rgb_image_padded_rows_skip_padding():
    # 2x2 RGB with stride 8: each row has 2 padding bytes (0xEE) that must be dropped.
    data = bytes([1, 2, 3, 4, 5, 6, 0xEE, 0xEE, 7, 8, 9, 10, 11, 12, 0xEE, 0xEE])
    img = rgb_image_from_bytes(data, 2, 2, 8)
    assert [img.getpixel(p) for p in [(0, 0), (1, 0), (0, 1), (1, 1)]] == [
        (1, 2, 3), (4, 5, 6), (7, 8, 9), (10, 11, 12)]


def test_pipeline_description():
    # The rate cap is negotiated with the producer via caps; a videorate element
    # downstream stalled on Hyprland's variable-rate stream (stuck on the first frame).
    assert pipeline_description(17, 42, 5) == (
        "pipewiresrc fd=17 path=42 keepalive-time=1000 ! "
        "video/x-raw,format=(string){BGRx,RGBx,xRGB,xBGR,BGRA,RGBA,ARGB,ABGR,BGR,RGB},"
        "max-framerate=5/1 ! "
        "appsink name=sink max-buffers=1 drop=true sync=false emit-signals=true"
    )


def test_request_path():
    assert request_path(":1.234", "vitality_ab12") == (
        "/org/freedesktop/portal/desktop/request/1_234/vitality_ab12")


def test_backoff_doubles_from_2s_capped_at_60():
    assert [backoff_delay(n) for n in range(1, 8)] == [2, 4, 8, 16, 32, 60, 60]


def test_user_cancel_stops_retrying():
    assert should_retry(PortalResponseError("Start", 1)) is False


def test_other_failures_keep_retrying():
    assert should_retry(PortalResponseError("Start", 2)) is True
    assert should_retry(PortalError("pipeline error")) is True
    assert should_retry(OSError("bus gone")) is True


def failing_sessions(calls, response_for):
    """A session_factory whose sessions record the token they were opened with, then fail."""
    class FailingSession(PortalSession):
        fd: int | None = None

        async def open(self, restore_token: str | None,
                       on_restore_token: Callable[[str], None]) -> list[Stream]:
            calls.append(restore_token)
            raise PortalResponseError("Start", response_for(restore_token))

        def lost(self) -> str | None:
            return None

        async def close(self) -> None:
            pass

    return FailingSession


async def test_cancel_with_restore_token_stops_without_second_dialog():
    calls = []
    src = PortalFrameSource(["tok"],
                            session_factory=failing_sessions(calls, lambda token: 1))
    await src.start(lambda i, token: None)
    assert src._task is not None
    await asyncio.wait_for(src._task, 1)         # supervisor ended: no further attempts
    assert calls == ["tok"] and src.available is False
    await src.close()


async def test_rejected_restore_token_retries_once_without_it():
    calls = []
    src = PortalFrameSource(["tok"],
                            session_factory=failing_sessions(calls, lambda token: 2 if token else 1))
    await src.start(lambda i, token: None)
    assert src._task is not None
    await asyncio.wait_for(src._task, 1)
    assert calls == ["tok", None]
    await src.close()


def test_rgb_image_accepts_a_memoryview_slice():
    data = bytes([0xEE, 0xEE, 1, 2, 3, 4, 5, 6, 0xEE, 0xEE, 7, 8, 9, 10, 11, 12, 0xEE, 0xEE])
    with memoryview(data) as view, view[2:] as tail:
        img = rgb_image_from_bytes(tail, 2, 2, 8)
    assert [img.getpixel(p) for p in [(0, 0), (1, 0), (0, 1), (1, 1)]] == [
        (1, 2, 3), (4, 5, 6), (7, 8, 9), (10, 11, 12)]


def test_sample_to_image_from_a_real_gstreamer_buffer():
    from gi.repository import Gst
    from vitality_client.daemon.frames.portal.stream import sample_to_image as _sample_to_image

    Gst.init(None)
    pixels = bytes([1, 2, 3, 4, 5, 6, 0xEE, 0xEE, 7, 8, 9, 10, 11, 12, 0xEE, 0xEE])
    sample = Gst.Sample.new(
        Gst.Buffer.new_wrapped(pixels),
        Gst.Caps.from_string("video/x-raw,format=RGB,width=2,height=2"), None, None)
    img = _sample_to_image(sample)
    assert img.size == (2, 2)
    assert [img.getpixel(p) for p in [(0, 0), (1, 0), (0, 1), (1, 1)]] == [
        (1, 2, 3), (4, 5, 6), (7, 8, 9), (10, 11, 12)]


def test_raw_mode_for_every_negotiable_format():
    # Alpha bytes are ignored: screens are opaque, so A is read as padding.
    assert {f: raw_mode_for(f) for f in
            ["BGRx", "RGBx", "xRGB", "xBGR", "BGRA", "RGBA", "ARGB", "ABGR", "BGR", "RGB"]} == {
        "BGRx": "BGRX", "RGBx": "RGBX", "xRGB": "XRGB", "xBGR": "XBGR",
        "BGRA": "BGRX", "RGBA": "RGBX", "ARGB": "XRGB", "ABGR": "XBGR",
        "BGR": "BGR", "RGB": "RGB"}


def test_sample_to_image_converts_bgrx_from_a_real_gstreamer_buffer():
    from gi.repository import Gst
    from vitality_client.daemon.frames.portal.stream import sample_to_image as _sample_to_image

    Gst.init(None)
    pixels = bytes([3, 2, 1, 0xFF, 6, 5, 4, 0xFF])  # B, G, R, x per pixel
    sample = Gst.Sample.new(
        Gst.Buffer.new_wrapped(pixels),
        Gst.Caps.from_string("video/x-raw,format=BGRx,width=2,height=1"), None, None)
    img = _sample_to_image(sample)
    assert img.mode == "RGB" and [img.getpixel((0, 0)), img.getpixel((1, 0))] == [(1, 2, 3), (4, 5, 6)]


def test_frames_are_converted_only_when_requested(monkeypatch):
    # The stream delivers several frames a second; only snapshots need an image.
    from gi.repository import Gst
    from vitality_client.daemon.frames.portal import stream
    from vitality_client.daemon.frames.portal.session import Stream

    Gst.init(None)
    conversions = []
    real = stream.sample_to_image
    monkeypatch.setattr(stream, "sample_to_image", lambda s: conversions.append(s) or real(s))
    src = stream.GstFrameStreams(max_fps=5)
    src._streams = [Stream(node_id=1, position=(0, 0), size=None)]
    caps = Gst.Caps.from_string("video/x-raw,format=BGRx,width=1,height=1")
    for value in (1, 2, 3):
        sample = Gst.Sample.new(Gst.Buffer.new_wrapped(bytes([value, value, value, 0])), caps, None, None)
        sink = type("Sink", (), {"emit": lambda self, name, s=sample: s})()
        src._on_sample(sink, 0)
    assert conversions == []
    frames = src.latest_frames()
    assert frames is not None and frames[0].image.getpixel((0, 0)) == (3, 3, 3)
    assert frames[0].number is None
    assert len(conversions) == 1


class RecordingStreams:
    """FrameStreams that records what it was asked to read (no GStreamer)."""

    def __init__(self, max_fps):
        self.started = []

    def __len__(self):
        return sum(len(streams) for _, streams in self.started)

    def start(self, fd, streams):
        self.started.append((fd, streams))

    def latest_frames(self):
        return None

    def failure(self):
        return None

    def stop(self):
        self.started = []


def working_sessions(opened):
    """A session_factory whose n-th session shares monitor n, issuing token `new-<n>`."""
    class WorkingSession(PortalSession):
        def __init__(self):
            self.fd: int | None = None

        async def open(self, restore_token, on_restore_token):
            n = len(opened)
            opened.append(restore_token)
            on_restore_token(f"new-{n}")
            self.fd = 100 + n
            return [Stream(node_id=n, position=(1920 * n, 0), size=(1920, 1080))]

        def lost(self):
            return None

        async def close(self):
            pass

    return WorkingSession


async def test_one_session_per_screen_each_with_its_own_token():
    opened, saved = [], []
    src = PortalFrameSource(["tok-0"], sessions=2, session_factory=working_sessions(opened),
                            streams=RecordingStreams)
    await src.start(lambda i, token: saved.append((i, token)))
    assert opened == ["tok-0", None]                   # session 2 has no token yet: picker
    assert saved == [(0, "new-0"), (1, "new-1")]
    assert src.available and src.screens == 2
    assert [fd for fd, _ in src._streams.started] == [100, 101]   # type: ignore[attr-defined]
    await src.close()
    assert src.screens == 0


async def test_more_saved_tokens_than_sessions_are_ignored():
    opened = []
    src = PortalFrameSource(["a", "b"], sessions=1, session_factory=working_sessions(opened),
                            streams=RecordingStreams)
    await src.start(lambda i, token: None)
    assert opened == ["a"]
    await src.close()
