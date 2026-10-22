"""When to take screenshots — pure logic, no I/O.

Feed it activity timestamps (`on_activity`), clock ticks (`on_tick`) and pause
commands; it returns `SnapshotRequest`s saying "take a before/after shot now".
`next_deadline()` says when `on_tick` next has work, so callers can sleep until then.

Rules (spec §4): the first input after a quiet spell → `before`; `settle_seconds`
without input → `after`; during continuous activity, an `after` every
`max_gap_seconds`; pausing mid-interaction takes one final `after`.
"""

from vitality_client.policy.capture_policy import CapturePolicy, SnapshotRequest

__all__ = ["CapturePolicy", "SnapshotRequest"]
