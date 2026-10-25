"""The contract for deciding when to take screenshots."""

from dataclasses import dataclass
from typing import Literal, Protocol


@dataclass(frozen=True)
class InteractionRef:
    """Names one interaction by its first input; the daemon maps it to the server's id."""

    start: float  # first input, wall-clock epoch seconds


@dataclass(frozen=True)
class Snapshot:
    """'Take a screenshot now' for the given interaction."""

    interaction: InteractionRef
    trigger: Literal["activity", "manual"]
    ts: float  # wall-clock epoch seconds


@dataclass(frozen=True)
class InteractionEnd:
    """'This interaction is over': no further snapshot will reference it."""

    interaction: InteractionRef
    ts: float  # wall-clock epoch seconds


Action = Snapshot | InteractionEnd


class SnapshotPolicy(Protocol):
    """Turns activity, clock ticks, pause and manual commands into actions. No I/O."""

    @property
    def state(self) -> Literal["idle", "active", "paused"]:
        """idle: no recent input; active: an interaction is in progress; paused: capture is off."""
        ...

    @property
    def paused_until(self) -> float | None:
        """When a timed pause ends (epoch seconds), or None."""
        ...

    def on_activity(self, now: float) -> list[Action]:
        """Input happened at `now`; returns the opening `Snapshot` if it starts an interaction."""
        ...

    def on_tick(self, now: float) -> list[Action]:
        """Time passed; returns the settle `Snapshot` + `InteractionEnd`, or a max-gap `Snapshot`.

        Also handles timed-pause expiry (which returns nothing).
        """
        ...

    def pause(self, now: float, until: float | None = None) -> list[Action]:
        """Stop capturing (until `until`, if given); returns the final `Snapshot` + `InteractionEnd` if one was open."""
        ...

    def manual(self, now: float) -> list[Action]:
        """User asked for a screenshot; never changes `state` or timers.

        Active: returns one `Snapshot` in the open interaction. Idle or paused: returns a
        `Snapshot` + `InteractionEnd` for a one-shot interaction starting at `now`.
        """
        ...

    def resume(self) -> None:
        """Capture again from the next activity."""
        ...

    def next_deadline(self) -> float | None:
        """The earliest time `on_tick` has work to do, or None if nothing is pending."""
        ...
