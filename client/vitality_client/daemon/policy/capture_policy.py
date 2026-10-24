"""Pure timing logic deciding when to take before/after screenshots. No I/O."""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from vitality_client.daemon.policy.interface import SnapshotPolicy, SnapshotRequest


class CapturePolicy(SnapshotPolicy):
    def __init__(
        self,
        settle_seconds: float,
        max_gap_seconds: float,
        new_id: Callable[[], str],
        paused: bool = False,
        paused_until: float | None = None,
    ) -> None:
        self._settle = settle_seconds
        self._max_gap = max_gap_seconds
        self._new_id = new_id
        self._state: Literal["idle", "active", "paused"] = "paused" if paused else "idle"
        self._paused_until = paused_until if paused else None
        self._interaction_id = ""
        self._last_activity = 0.0
        self._last_snapshot = 0.0

    @property
    def state(self) -> Literal["idle", "active", "paused"]:
        return self._state

    @property
    def paused_until(self) -> float | None:
        return self._paused_until

    def on_activity(self, now: float) -> list[SnapshotRequest]:
        out = self._process_due(now)
        if self._state == "idle":
            self._interaction_id = self._new_id()
            self._last_activity = now
            self._last_snapshot = now
            self._state = "active"
            out.append(SnapshotRequest("before", self._interaction_id, now))
        elif self._state == "active":
            self._last_activity = now
        return out

    def on_tick(self, now: float) -> list[SnapshotRequest]:
        return self._process_due(now)

    def pause(self, now: float, until: float | None = None) -> list[SnapshotRequest]:
        if self._state == "paused":
            self._paused_until = until
            return []
        out = self._process_due(now)
        if self._state == "active" and not out:
            out.append(SnapshotRequest("after", self._interaction_id, now))
        self._state = "paused"
        self._paused_until = until
        return out

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

    def _process_due(self, now: float) -> list[SnapshotRequest]:
        if self._state == "paused":
            if self._paused_until is not None and now >= self._paused_until:
                self.resume()
            return []
        if self._state != "active":
            return []
        if now < self._last_snapshot:
            # Clock stepped backwards: rebase so deadlines proceed from here.
            self._last_snapshot = now
            self._last_activity = now
            return []
        if now - self._last_activity >= self._settle:
            self._state = "idle"
            return [SnapshotRequest("after", self._interaction_id, now)]
        if now - self._last_snapshot >= self._max_gap:
            self._last_snapshot = now
            return [SnapshotRequest("after", self._interaction_id, now)]
        return []
