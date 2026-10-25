"""Implements `SnapshotPolicy` (see interface.py).

Pure timing logic deciding when to take screenshots and where interactions end. No I/O.
`CapturePolicy` has no public methods beyond the Protocol's; its constructor takes the
`settle_seconds` and `max_gap_seconds` from the config and, to restart paused, the saved
pause state.
"""

from __future__ import annotations

from typing import Literal

from vitality_client.daemon.policy.interface import (
    Action,
    InteractionEnd,
    InteractionRef,
    Snapshot,
    SnapshotPolicy,
)


class CapturePolicy(SnapshotPolicy):
    def __init__(
        self,
        settle_seconds: float,
        max_gap_seconds: float,
        paused: bool = False,
        paused_until: float | None = None,
    ) -> None:
        self._settle = settle_seconds
        self._max_gap = max_gap_seconds
        self._state: Literal["idle", "active", "paused"] = "paused" if paused else "idle"
        self._paused_until = paused_until if paused else None
        self._open: InteractionRef | None = None
        self._last_activity = 0.0
        self._last_snapshot = 0.0

    @property
    def state(self) -> Literal["idle", "active", "paused"]:
        return self._state

    @property
    def paused_until(self) -> float | None:
        return self._paused_until

    def on_activity(self, now: float) -> list[Action]:
        out = self._process_due(now)
        if self._state == "idle":
            self._open = InteractionRef(now)
            self._last_activity = now
            self._last_snapshot = now
            self._state = "active"
            out.append(Snapshot(self._open, "activity", now))
        elif self._state == "active":
            self._last_activity = now
        return out

    def on_tick(self, now: float) -> list[Action]:
        return self._process_due(now)

    def pause(self, now: float, until: float | None = None) -> list[Action]:
        if self._state == "paused":
            self._paused_until = until
            return []
        out = self._process_due(now)
        if self._state == "active" and self._open is not None:
            if not out:
                out.append(Snapshot(self._open, "activity", now))
            out.append(InteractionEnd(self._open, now))
            self._open = None
        self._state = "paused"
        self._paused_until = until
        return out

    def manual(self, now: float) -> list[Action]:
        if self._state == "active" and self._open is not None:
            return [Snapshot(self._open, "manual", now)]
        ref = InteractionRef(now)
        return [Snapshot(ref, "manual", now), InteractionEnd(ref, now)]

    def resume(self) -> None:
        if self._state == "paused":
            self._state = "idle"
            self._paused_until = None

    def next_deadline(self) -> float | None:
        if self._state == "active":
            return min(
                self._last_activity + self._settle,
                self._last_snapshot + self._max_gap,
            )
        if self._state == "paused":
            return self._paused_until
        return None

    def _process_due(self, now: float) -> list[Action]:
        if self._state == "paused":
            if self._paused_until is not None and now >= self._paused_until:
                self.resume()
            return []
        if self._state != "active" or self._open is None:
            return []
        if now < self._last_snapshot:
            # Clock stepped backwards: rebase so deadlines proceed from here.
            self._last_snapshot = now
            self._last_activity = now
            return []
        if now - self._last_activity >= self._settle:
            ref, self._open = self._open, None
            self._state = "idle"
            return [Snapshot(ref, "activity", now), InteractionEnd(ref, now)]
        if now - self._last_snapshot >= self._max_gap:
            self._last_snapshot = now
            return [Snapshot(self._open, "activity", now)]
        return []
