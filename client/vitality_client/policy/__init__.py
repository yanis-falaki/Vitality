"""When to take screenshots — pure logic, no I/O.

Contract: `SnapshotPolicy` and `SnapshotRequest` (interface.py).
Implementation: `CapturePolicy` (capture_policy.py).

Rules (spec §4): the first input after a quiet spell → `before`; `settle_seconds`
without input → `after`; during continuous activity, an `after` every
`max_gap_seconds`; pausing mid-interaction takes one final `after`.
"""

from vitality_client.policy.capture_policy import CapturePolicy
from vitality_client.policy.interface import SnapshotPolicy, SnapshotRequest

__all__ = ["CapturePolicy", "SnapshotPolicy", "SnapshotRequest"]
