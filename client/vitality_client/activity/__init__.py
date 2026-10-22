"""Input activity: reports THAT the user touched a keyboard, mouse or touchpad, and when.

`ActivityWatcher(on_activity)` reads every input device via evdev and calls
`on_activity(timestamp)` — at most once per 0.1 s — and nothing else: key codes,
characters and pointer positions never leave this package. `await run()` until
cancelled; `available` is False when no input device could be opened.
"""

from vitality_client.activity.evdev_watcher import ActivityWatcher, is_activity_device

__all__ = ["ActivityWatcher", "is_activity_device"]
