"""When to take screenshots and where interactions end — pure logic, no I/O.

Contract: `SnapshotPolicy`, `Snapshot`, `InteractionEnd`, `InteractionRef` (interface.py).
Implementation: `CapturePolicy` (capture_policy.py).

Rules (spec §4): the first input after a quiet spell opens an interaction, named by
its `InteractionRef(start)`, with a `Snapshot`; `settle_seconds` without input →
a final `Snapshot` + `InteractionEnd`; during continuous activity, a `Snapshot`
every `max_gap_seconds`; pausing mid-interaction takes a final `Snapshot` +
`InteractionEnd`. A manual request joins the open interaction with one `Snapshot`,
or, when idle or paused, is a one-shot interaction (`Snapshot` + `InteractionEnd`)
and changes no state or timers.
"""

from vitality_client.daemon.policy.capture_policy import CapturePolicy
from vitality_client.daemon.policy.interface import (
    Action,
    InteractionEnd,
    InteractionRef,
    Snapshot,
    SnapshotPolicy,
)

__all__ = [
    "Action",
    "CapturePolicy",
    "InteractionEnd",
    "InteractionRef",
    "Snapshot",
    "SnapshotPolicy",
]
