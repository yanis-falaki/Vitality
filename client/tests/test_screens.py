from PIL import Image, ImageDraw

from vitality_client.daemon.focus import HyprlandFocus, NoFocus, focus_hint, match_focused
from vitality_client.daemon.frames import ScreenFrame
from vitality_client.daemon.policy import changed, screens_to_capture, thumbnail


def page(lines: int, offset: int = 0) -> Image.Image:
    """A 1920×1080 white page with `lines` text-like dark bars, shifted down by `offset`."""
    img = Image.new("RGB", (1920, 1080), "white")
    draw = ImageDraw.Draw(img)
    for i in range(lines):
        y = 40 + offset + i * 36
        draw.rectangle((60, y, 60 + 900 - (i * 37) % 400, y + 14), fill="black")
    return img


def test_cursor_blink_and_clock_tick_are_not_changes():
    before = page(20)
    after = before.copy(); draw = ImageDraw.Draw(after)
    draw.rectangle((970, 400, 971, 418), fill="black")          # a text cursor appears
    draw.rectangle((1850, 1060, 1858, 1074), fill="black")      # a clock digit changes
    draw.polygon([(500, 500), (500, 524), (517, 517)], fill="black")  # the mouse pointer
    assert not changed(thumbnail(before), thumbnail(after))


def test_scrolling_and_a_new_message_are_changes():
    assert changed(thumbnail(page(20)), thumbnail(page(20, offset=18)))       # scrolled
    with_message = page(20); ImageDraw.Draw(with_message).rectangle((60, 800, 700, 814), fill="black")
    assert changed(thumbnail(page(20)), thumbnail(with_message))              # one new line
    assert changed(thumbnail(page(20)), thumbnail(Image.new("RGB", (1920, 1080), "navy")))


A, B, C = b"\x00" * 2304, b"\xff" * 2304, b"\x80" * 2304


def test_first_shot_takes_every_screen():
    assert screens_to_capture({1: A, 2: A}, {}, None) == [1, 2]


def test_one_screen_is_always_taken():
    assert screens_to_capture({None: A}, {None: A}, None) == [None]


def test_idle_second_screen_is_skipped_changed_one_taken():
    assert screens_to_capture({1: B, 2: A}, {1: A, 2: A}, None) == [1]


def test_nothing_qualifies_takes_nothing():
    assert screens_to_capture({1: A, 2: A}, {1: A, 2: A}, None) == []


def test_focused_screen_always_taken_as_focus_moves():
    last = {1: A, 2: A}
    assert screens_to_capture({1: A, 2: A}, last, 1) == [1]
    assert screens_to_capture({1: A, 2: A}, last, 2) == [2]
    # Focus moves 1 → 2 while screen 1 also changes: both.
    assert screens_to_capture({1: C, 2: A}, last, 2) == [1, 2]


def test_a_screen_not_taken_yet_is_taken():
    assert screens_to_capture({1: A, 2: A}, {1: A}, None) == [2]


def frame(number, position, size=(1920, 1080), image_size=None):
    return ScreenFrame(number, Image.new("RGB", image_size or size), position, size)


SCREENS = [frame(1, (0, 0)), frame(2, (1920, 0), (1280, 720), (2560, 1440))]


def monitors(focused_index, **changes):
    ms = [{"x": 0, "y": 0, "width": 1920, "height": 1080, "scale": 1.0, "focused": False},
          {"x": 1920, "y": 0, "width": 2560, "height": 1440, "scale": 2.0, "focused": False}]
    ms[focused_index]["focused"] = True
    ms[focused_index].update(changes)
    return ms


def test_hyprland_focus_matches_by_position():
    assert match_focused(monitors(0), SCREENS) == 1
    assert match_focused(monitors(1), SCREENS) == 2


def test_hyprland_focus_falls_back_to_size_when_positions_are_unknown():
    unplaced = [frame(1, (0, 0)), frame(2, (0, 0), (1280, 720), (2560, 1440))]
    assert match_focused(monitors(1), unplaced) == 2                  # logical 1280×720
    same = [frame(1, (0, 0)), frame(2, (0, 0))]
    assert match_focused(monitors(0), same) is None                  # ambiguous: no hint


def test_hyprland_focus_failures_give_no_hint():
    assert HyprlandFocus(lambda: "not json").focused_screen(SCREENS) is None
    assert HyprlandFocus(lambda: '[{"focused": true}]').focused_screen(SCREENS) is None

    def broken() -> str:
        raise FileNotFoundError("hyprctl")
    assert HyprlandFocus(broken).focused_screen(SCREENS) is None
    assert match_focused({"x": 1}, SCREENS) is None


def test_focus_hint_only_on_hyprland(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/hyprctl")
    assert isinstance(focus_hint({"HYPRLAND_INSTANCE_SIGNATURE": "abc"}), HyprlandFocus)
    assert isinstance(focus_hint({}), NoFocus)
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert isinstance(focus_hint({"HYPRLAND_INSTANCE_SIGNATURE": "abc"}), NoFocus)
