"""The contract for deciding when to take screenshots."""

from dataclasses import dataclass
from typing import Literal, Protocol


@dataclass(frozen=True)
class SnapshotRequest:
    """'Take a screenshot now': a before or after shot belonging to one interaction."""

    kind: Literal["before", "after"]
    interaction_id: str
    ts: float  # wall-clock epoch seconds


class SnapshotPolicy(Protocol):
    """Turns activity, clock ticks and pause commands into snapshot requests. No I/O."""

    @property
    def state(self) -> Literal["idle", "active", "paused"]:
        """idle: no recent input; active: an interaction is in progress; paused: capture is off."""
        ...

    @property
    def paused_until(self) -> float | None:
        """When a timed pause ends (epoch seconds), or None."""
        ...

    def on_activity(self, now: float) -> list[SnapshotRequest]:
        """Input happened at `now`; may start an interaction (a `before` shot)."""
        ...

    def on_tick(self, now: float) -> list[SnapshotRequest]:
        """Time passed; handles settle (`after`), max gap (`after`) and timed-pause expiry."""
        ...

    def pause(self, now: float, until: float | None = None) -> list[SnapshotRequest]:
        """Stop capturing (until `until`, if given); closes an open interaction with an `after`."""
        ...

    def resume(self) -> None:
        """Capture again from the next activity."""
        ...

    def next_deadline(self) -> float | None:
        """The earliest time `on_tick` has work to do, or None if nothing is pending."""
        ...
