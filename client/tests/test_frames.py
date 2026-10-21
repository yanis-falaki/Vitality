from PIL import Image

from vitality_client.frames.base import composite
from vitality_client.frames.portal import (
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
    assert pipeline_description(17, 42, 5) == (
        "pipewiresrc fd=17 path=42 always-copy=true keepalive-time=1000 ! "
        "videorate max-rate=5 ! videoconvert ! video/x-raw,format=RGB ! "
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
