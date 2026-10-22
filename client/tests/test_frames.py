import asyncio

from PIL import Image

from vitality_client.frames.base import composite, layout_frames
from vitality_client.frames.portal import (
    PortalFrameSource,
    PortalError,
    PortalResponseError,
    backoff_delay,
    pipeline_description,
    request_path,
    rgb_image_from_bytes,
    should_retry,
)


def test_composite_single_frame_unchanged():
    a = Image.new("RGB", (10, 5), "red"); assert composite([(a, (0, 0))]) is a


def test_composite_side_by_side():
    a = Image.new("RGB", (10, 5), "red"); b = Image.new("RGB", (4, 8), "blue")
    out = composite([(a, (0, 0)), (b, (10, 0))])
    assert out.size == (14, 8) and out.getpixel((0, 0)) == (255, 0, 0) and out.getpixel((12, 7)) == (0, 0, 255)
    assert out.getpixel((5, 7)) == (0, 0, 0)


def test_composite_negative_positions():
    a = Image.new("RGB", (10, 5), "red"); b = Image.new("RGB", (10, 5), "blue")
    out = composite([(a, (-10, 0)), (b, (0, 0))])
    assert out.size == (20, 5) and out.getpixel((0, 0)) == (255, 0, 0) and out.getpixel((10, 0)) == (0, 0, 255)


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
        "video/x-raw,max-framerate=5/1 ! videoconvert ! video/x-raw,format=RGB ! "
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


def test_layout_single_monitor_is_noop():
    a = Image.new("RGB", (20, 10), "red")
    assert layout_frames([(a, (0, 0), (20, 10))]) == [(a, (0, 0))]


def test_layout_hidpi_frame_alone_keeps_physical_pixels():
    a = Image.new("RGB", (40, 20), "red")        # scale 2: logical 20x10
    out = layout_frames([(a, (0, 0), (20, 10))])
    assert out[0][0] is a and out[0][1] == (0, 0)


def test_layout_mixed_scales_side_by_side_do_not_overlap():
    a = Image.new("RGB", (40, 20), "red")        # scale 2, logical 20x10 at (0, 0)
    b = Image.new("RGB", (20, 10), "blue")       # scale 1, logical 20x10 at (20, 0)
    laid = layout_frames([(a, (0, 0), (20, 10)), (b, (20, 0), (20, 10))])
    assert laid[0] == (a, (0, 0))
    assert laid[1][0].size == (40, 20) and laid[1][1] == (40, 0)
    out = composite(laid)
    assert out.size == (80, 20)
    assert out.getpixel((39, 19)) == (255, 0, 0) and out.getpixel((40, 0)) == (0, 0, 255)


def test_layout_fractional_scale_rounds():
    a = Image.new("RGB", (1920, 1080), "red")    # scale 1.5, logical 1280x720 at (0, 0)
    b = Image.new("RGB", (1920, 1080), "blue")   # scale 1, logical 1920x1080 at (1280, 0)
    laid = layout_frames([(a, (0, 0), (1280, 720)), (b, (1280, 0), (1920, 1080))])
    assert laid[0] == (a, (0, 0))
    assert laid[1][0].size == (2880, 1620) and laid[1][1] == (1920, 0)
    assert composite(laid).size == (4800, 1620)


def test_layout_odd_logical_size_maps_to_own_frame():
    a = Image.new("RGB", (2560, 1440), "red")    # scale 1.5 with a rounded logical width
    laid = layout_frames([(a, (0, 0), (1707, 960)), (Image.new("RGB", (8, 8)), (1707, 0), (8, 8))])
    assert laid[0][0] is a and laid[1][1] == (2560, 0)


async def test_cancel_with_restore_token_stops_without_second_dialog():
    src = PortalFrameSource("tok", lambda t: None)
    calls = []

    async def fake_open_session(token):
        calls.append(token)
        raise PortalResponseError("Start", 1)

    src._open_session = fake_open_session
    await src.start()
    await asyncio.wait_for(src._task, 1)         # supervisor ended: no further attempts
    assert calls == ["tok"] and src.available is False
    await src.close()


async def test_rejected_restore_token_retries_once_without_it():
    src = PortalFrameSource("tok", lambda t: None)
    calls = []

    async def fake_open_session(token):
        calls.append(token)
        raise PortalResponseError("Start", 2 if token else 1)

    src._open_session = fake_open_session
    await src.start()
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
    from vitality_client.frames.portal import _sample_to_image

    Gst.init(None)
    pixels = bytes([1, 2, 3, 4, 5, 6, 0xEE, 0xEE, 7, 8, 9, 10, 11, 12, 0xEE, 0xEE])
    sample = Gst.Sample.new(
        Gst.Buffer.new_wrapped(pixels),
        Gst.Caps.from_string("video/x-raw,format=RGB,width=2,height=2"), None, None)
    img = _sample_to_image(sample)
    assert img.size == (2, 2)
    assert [img.getpixel(p) for p in [(0, 0), (1, 0), (0, 1), (1, 1)]] == [
        (1, 2, 3), (4, 5, 6), (7, 8, 9), (10, 11, 12)]
