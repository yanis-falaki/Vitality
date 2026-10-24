"""Input-activity watcher.

Reports ONLY that input happened and when (a float timestamp). Event objects are
inspected for their type inside the read loop and never passed on, stored, or
logged; key codes, characters and pointer data do not leave this module.
"""

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any

import evdev
from evdev import ecodes

from vitality_client.daemon.activity.interface import ActivitySource

log = logging.getLogger(__name__)

_ACTIVITY_EVENT_TYPES = frozenset({ecodes.EV_KEY, ecodes.EV_REL, ecodes.EV_ABS})
_KEYBOARD_KEYS = frozenset(
    [getattr(ecodes, f"KEY_{c}") for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"] + [ecodes.KEY_SPACE]
)
_POINTER_BUTTONS = frozenset({ecodes.BTN_LEFT, ecodes.BTN_TOUCH})


def _codes(entries: list) -> set[int]:
    # evdev reports EV_ABS entries as (code, AbsInfo) tuples, others as plain codes.
    return {e[0] if isinstance(e, tuple) else e for e in entries}


def is_activity_device(capabilities: dict[int, list]) -> bool:
    """True for keyboards, mice and touchpads; False for power buttons, lid switches, etc."""
    keys = _codes(capabilities.get(ecodes.EV_KEY, []))
    if keys & _KEYBOARD_KEYS or keys & _POINTER_BUTTONS:
        return True
    return ecodes.REL_X in _codes(capabilities.get(ecodes.EV_REL, []))


class ActivityWatcher(ActivitySource):
    def __init__(
        self,
        list_devices: Callable[[], list[str]] = evdev.list_devices,
        open_device: Callable[[str], Any] = evdev.InputDevice,
        clock: Callable[[], float] = time.time,
        rescan_seconds: float = 5,
        min_interval: float = 0.1,
    ) -> None:
        self._on_activity: Callable[[float], None] = lambda now: None  # set by run()
        self._list_devices = list_devices
        self._open_device = open_device
        self._clock = clock
        self._rescan_seconds = rescan_seconds
        # Report at most one activity per interval: a 1000 Hz mouse would otherwise call
        # into the capture logic 1000 times a second. The first event after a quiet
        # spell is always reported at once.
        self._min_interval = min_interval
        self._last_report: float | None = None
        self._devices: dict[str, Any] = {}
        self._readers: dict[str, asyncio.Task] = {}
        self.available = False

    async def run(self, on_activity: Callable[[float], None]) -> None:
        self._on_activity = on_activity
        try:
            while True:
                self._scan()
                await asyncio.sleep(self._rescan_seconds)
        finally:
            await self._shutdown()

    def _scan(self) -> None:
        try:
            paths = self._list_devices()
        except OSError as exc:
            # A device unplugged mid-listing must not end the watcher; skip this round.
            log.debug("cannot list input devices: %s", exc.strerror or type(exc).__name__)
            return
        for path in paths:
            if path in self._devices:
                continue
            try:
                device = self._open_device(path)
            except PermissionError:
                log.debug("no permission to open input device %s", path)
                continue
            except OSError as exc:
                log.debug("cannot open input device %s: %s", path, exc.strerror or type(exc).__name__)
                continue
            try:
                watch = is_activity_device(device.capabilities())
            except OSError:
                watch = False
            if not watch:
                self._close(device)
                continue
            log.info("watching input device %s (%s)", path, getattr(device, "name", "?"))
            self._devices[path] = device
            self._readers[path] = asyncio.create_task(self._read(path, device))
        self.available = bool(self._devices)

    def _report(self, now: float) -> None:
        last = self._last_report
        if last is not None and 0 <= now - last < self._min_interval:
            return
        self._last_report = now
        self._on_activity(now)

    async def _read(self, path: str, device: Any) -> None:
        try:
            async for event in device.async_read_loop():
                if event.type in _ACTIVITY_EVENT_TYPES:
                    self._report(float(self._clock()))
        except OSError:
            log.info("input device %s went away", path)
        except Exception as exc:
            log.error("input device %s reader failed (%s)", path, type(exc).__name__)
        finally:
            self._forget(path, device)

    def _forget(self, path: str, device: Any) -> None:
        if self._devices.get(path) is device:
            del self._devices[path]
            self._readers.pop(path, None)
        self._close(device)
        self.available = bool(self._devices)

    @staticmethod
    def _close(device: Any) -> None:
        try:
            device.close()
        except Exception:
            pass

    async def _shutdown(self) -> None:
        readers = list(self._readers.values())
        for task in readers:
            task.cancel()
        await asyncio.gather(*readers, return_exceptions=True)
        for device in list(self._devices.values()):
            self._close(device)
        self._devices.clear()
        self._readers.clear()
        self.available = False
