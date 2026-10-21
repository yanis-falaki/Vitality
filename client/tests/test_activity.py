import asyncio
from types import SimpleNamespace

from evdev import AbsInfo
from evdev.ecodes import (
    ABS_X, ABS_Y, BTN_LEFT, BTN_TOUCH, EV_ABS, EV_KEY, EV_REL, EV_SW, EV_SYN,
    KEY_A, KEY_POWER, KEY_SPACE, REL_X, REL_Y, SW_LID,
)

from vitality_client.activity import ActivityWatcher, is_activity_device

KEYBOARD_CAPS = {EV_KEY: [KEY_A, KEY_SPACE], EV_SYN: [0]}
MOUSE_CAPS = {EV_KEY: [BTN_LEFT], EV_REL: [REL_X, REL_Y]}
TOUCHPAD_CAPS = {EV_KEY: [BTN_TOUCH],
                 EV_ABS: [(ABS_X, AbsInfo(0, 0, 100, 0, 0, 0)),
                          (ABS_Y, AbsInfo(0, 0, 100, 0, 0, 0))]}
POWER_BUTTON_CAPS = {EV_KEY: [KEY_POWER]}
LID_SWITCH_CAPS = {EV_SW: [SW_LID]}


def ev(type_, code, value):
    return SimpleNamespace(type=type_, code=code, value=value)


class FakeDevice:
    def __init__(self, caps, events=(), raise_after=None):
        self._caps, self._events, self._raise_after = caps, list(events), raise_after
        self.closed = False
        self.name = "fake"

    def capabilities(self):
        return self._caps

    def close(self):
        self.closed = True

    async def async_read_loop(self):
        for i, e in enumerate(self._events):
            if self._raise_after is not None and i == self._raise_after:
                raise OSError(19, "No such device")
            yield e
        if self._raise_after is not None and self._raise_after >= len(self._events):
            raise OSError(19, "No such device")
        await asyncio.sleep(3600)  # stay open, like a real idle device


async def run_briefly(w, seconds=0.05):
    task = asyncio.create_task(w.run())
    await asyncio.sleep(seconds)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def test_events_reported_as_timestamp_only():
    seen = []
    dev = FakeDevice(KEYBOARD_CAPS, events=[ev(EV_KEY, KEY_A, 1), ev(EV_KEY, KEY_A, 0)])
    w = ActivityWatcher(seen.append, lambda: ["/dev/input/event3"], lambda p: dev, clock=lambda: 42.0)
    await run_briefly(w)
    assert seen == [42.0, 42.0] and all(type(x) is float for x in seen)


async def test_sync_events_ignored():
    seen = []; dev = FakeDevice(KEYBOARD_CAPS, events=[ev(EV_SYN, 0, 0)])
    await run_briefly(ActivityWatcher(seen.append, lambda: ["/e"], lambda p: dev))
    assert seen == []


async def test_rel_and_abs_events_reported():
    seen = []
    dev = FakeDevice(MOUSE_CAPS, events=[ev(EV_REL, REL_X, 5), ev(EV_ABS, ABS_X, 9)])
    await run_briefly(ActivityWatcher(seen.append, lambda: ["/e"], lambda p: dev, clock=lambda: 1.0))
    assert seen == [1.0, 1.0]


def test_device_classification():
    assert is_activity_device(KEYBOARD_CAPS) and is_activity_device(MOUSE_CAPS) and is_activity_device(TOUCHPAD_CAPS)
    assert not is_activity_device(POWER_BUTTON_CAPS)
    assert not is_activity_device(LID_SWITCH_CAPS)
    assert not is_activity_device({})


async def test_non_activity_device_not_watched_and_closed():
    dev = FakeDevice(POWER_BUTTON_CAPS, events=[ev(EV_KEY, KEY_POWER, 1)])
    seen = []
    w = ActivityWatcher(seen.append, lambda: ["/e"], lambda p: dev)
    await run_briefly(w)
    assert seen == [] and dev.closed and w.available is False


async def test_permission_denied_marks_unavailable():
    def deny(p): raise PermissionError(p)
    w = ActivityWatcher(lambda t: None, lambda: ["/e"], deny); await run_briefly(w)
    assert w.available is False


async def test_available_when_device_open():
    dev = FakeDevice(KEYBOARD_CAPS)
    w = ActivityWatcher(lambda t: None, lambda: ["/e"], lambda p: dev)
    assert w.available is False
    task = asyncio.create_task(w.run())
    await asyncio.sleep(0.05)
    assert w.available is True
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def test_cancel_closes_devices_and_propagates():
    dev = FakeDevice(KEYBOARD_CAPS)
    w = ActivityWatcher(lambda t: None, lambda: ["/e"], lambda p: dev)
    task = asyncio.create_task(w.run())
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
        raised = False
    except asyncio.CancelledError:
        raised = True
    assert raised and dev.closed and w.available is False


async def test_unplugged_device_dropped_and_reopened():          # Review Focus 3
    seen = []; devs = {"/a": FakeDevice(MOUSE_CAPS, events=[ev(EV_REL, REL_X, 3)], raise_after=1),
                       "/b": FakeDevice(KEYBOARD_CAPS, events=[ev(EV_KEY, KEY_A, 1)])}
    opens = []
    def open_(p): opens.append(p); return devs[p]
    w = ActivityWatcher(seen.append, lambda: list(devs), open_, clock=lambda: 1.0, rescan_seconds=0.01)
    await run_briefly(w, seconds=0.1)
    assert seen.count(1.0) >= 2 and opens.count("/a") >= 2     # /b kept working; /a re-opened on rescan
    assert opens.count("/b") == 1                               # healthy device not re-opened
