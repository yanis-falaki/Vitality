"""Input activity: when the user touches a keyboard, mouse or touchpad — never what they typed.

Contract: `ActivitySource` (interface.py).
Implementation: `ActivityWatcher` (evdev_watcher.py) reads every input device via evdev
and calls `on_activity(timestamp)` at most once per 0.1 s. Key codes, characters and
pointer positions never leave this package.
"""

from vitality_client.daemon.activity.evdev_watcher import ActivityWatcher, is_activity_device
from vitality_client.daemon.activity.interface import ActivitySource

__all__ = ["ActivitySource", "ActivityWatcher", "is_activity_device"]
