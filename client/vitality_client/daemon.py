"""The running daemon: takes before/after screenshots around user activity.

The contract (`Daemon`, `Status`) comes first; the implementation (`CaptureDaemon`)
follows. CaptureDaemon wires the packages together: activity → policy → frame →
writer, plus a timer loop and the control socket. Everything runs on one asyncio loop;
image encoding and disk writes run in a worker thread, one at a time, in request order.
"""

import asyncio
import contextlib
import logging
import math
import signal
import time
from collections.abc import Callable
from datetime import datetime
from typing import Literal, Protocol, TypedDict

import ulid
from PIL import Image

from vitality_client.activity import ActivitySource
from vitality_client.config import Config, Paths
from vitality_client.control import ControlServer, State, StateFile, StateStore
from vitality_client.frames import FrameSource
from vitality_client.policy import CapturePolicy, SnapshotPolicy, SnapshotRequest
from vitality_client.storage import CaptureIndex, CaptureWriter, Store, Writer, format_ts


# == Contract =============================================================================

class StatusPaths(TypedDict):
    config: str  # client.toml
    data: str    # captures/ and index.sqlite
    state: str   # state.json


class Status(TypedDict):
    """The reply to a `status` request (and to `pause` / `resume`)."""

    ok: bool
    state: Literal["idle", "active", "paused"]
    paused_until: str | None   # UTC ISO timestamp when a timed pause ends
    captures_today: int        # since local midnight
    last_capture: str | None   # UTC ISO timestamp
    disk_bytes: int            # total size of saved captures
    disk_warning: bool         # above disk_warning_gb
    activity: Literal["available", "unavailable"]  # can input be read?
    capture: Literal["available", "unavailable"]   # is the screen stream up?
    paths: StatusPaths


class Daemon(Protocol):
    """Takes before/after screenshots around user activity until stopped.

    It owns a capture policy, a frame source, a writer and the control socket. Three
    things call into it: the activity source (`handle_activity`), the control socket
    (`handle`) and the frame source (`save_restore_token`).
    """

    async def run(self) -> None:
        """Start everything, run until SIGTERM/SIGINT, then shut down cleanly."""
        ...

    async def close(self) -> None:
        """Stop taking snapshots, finish pending writes, release everything."""
        ...

    def handle_activity(self, now: float) -> None:
        """The activity source's callback: the user touched an input device at `now`."""
        ...

    async def handle(self, request: dict) -> dict:
        """The control socket's handler: `{"cmd": "status" | "pause" | "resume", ...}`.

        Replies with the `Status`, or `{"ok": False, "error": ...}`.
        """
        ...

    def save_restore_token(self, token: str) -> None:
        """The frame source's callback: persist a new screen-sharing permission token."""
        ...

    def status(self) -> Status:
        """What the daemon is doing right now."""
        ...


# == Implementation =======================================================================

log = logging.getLogger(__name__)

DRAIN_TIMEOUT = 10.0


def _new_id() -> str:
    return str(ulid.ULID())


class CaptureDaemon(Daemon):
    def __init__(
        self,
        config: Config,
        paths: Paths,
        frames: FrameSource,
        watcher_factory: Callable[[Callable[[float], None]], ActivitySource],
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._config = config
        self._paths = paths
        self._frames = frames
        self._clock = clock
        # The concrete parts are chosen here; everything below uses them only
        # through their interfaces.
        self._state_file: StateStore = StateFile(paths.state_file)
        self._state = self._state_file.load()
        self._policy: SnapshotPolicy = CapturePolicy(
            config.settle_seconds, config.max_gap_seconds, _new_id,
            paused=self._state.paused, paused_until=self._state.paused_until,
        )
        self._store: CaptureIndex = Store(paths.data_dir / "index.sqlite")
        self._writer: CaptureWriter = Writer(paths.data_dir, self._store, config.webp_quality, _new_id)
        self._server = ControlServer(paths.socket_path, self.handle)
        self._watcher = watcher_factory(self.handle_activity)
        self._writes: asyncio.Queue[tuple[Image.Image, SnapshotRequest]] = asyncio.Queue()
        self._write_worker: asyncio.Task | None = None
        self._wake = asyncio.Event()  # set when the policy's next deadline moved earlier
        self._closing = False
        # Cached capture-tree size: scanned once at startup (off the loop), then
        # advanced by each written row, so status() never walks the tree.
        self._disk_bytes = 0
        self._disk_warned = False

    # -- lifecycle ---------------------------------------------------------------------

    async def run(self) -> None:
        """Run until SIGTERM/SIGINT (or cancellation), then shut down cleanly."""
        loop = asyncio.get_running_loop()
        stop = asyncio.Event()
        tasks: list[asyncio.Task] = []
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
        try:
            # Start the server first: it refuses to run beside a live daemon, whose
            # in-flight temp files cleanup_orphans() would otherwise delete.
            await self._server.start()
            removed = self._writer.cleanup_orphans()
            if removed:
                log.info("removed %d orphaned capture file(s)", removed)
            # Before any task starts, so no write can land mid-scan and be counted twice.
            self._record_disk_usage(await asyncio.to_thread(self._writer.disk_usage_bytes))
            # frames.start() may wait on the portal permission dialog: run it in the
            # background so control and activity are live immediately.
            tasks = [
                asyncio.create_task(self._start_frames()),
                asyncio.create_task(self._watcher.run()),
                asyncio.create_task(self._tick_loop()),
            ]
            critical = set(tasks[1:])
            stopper = asyncio.create_task(stop.wait())
            try:
                done, _ = await asyncio.wait(
                    critical | {stopper}, return_when=asyncio.FIRST_COMPLETED
                )
            finally:
                stopper.cancel()
            for task in done & critical:
                task.result()  # re-raises a crash; the finally below still shuts down
                raise RuntimeError("a daemon task stopped unexpectedly")
            log.info("shutting down")
        finally:
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.remove_signal_handler(sig)
            self._closing = True  # no new snapshots from here on
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self.close()

    async def close(self) -> None:
        """Finish pending writes (bounded), then close the server, frames and store."""
        self._closing = True
        try:
            await asyncio.wait_for(self.drain(), DRAIN_TIMEOUT)
        except TimeoutError:
            log.warning("gave up waiting for %d pending capture write(s)", self._writes.qsize())
        if self._write_worker is not None:
            self._write_worker.cancel()
            await asyncio.gather(self._write_worker, return_exceptions=True)
            self._write_worker = None
        await self._server.close()
        await self._frames.close()
        self._store.close()

    async def _start_frames(self) -> None:
        try:
            await self._frames.start()
        except Exception as exc:
            log.error("screen capture failed to start: %s", str(exc) or type(exc).__name__)

    async def _tick_loop(self) -> None:
        while True:
            self._wake.clear()
            deadline = self._policy.next_deadline()
            timeout = None if deadline is None else max(0.0, deadline - self._clock())
            # A past deadline gives timeout 0: processed on the next loop turn, and
            # on_tick always moves the deadline forward, so this never spins.
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(timeout):
                    await self._wake.wait()
            self._step(self._policy.on_tick, self._clock())

    # -- activity and snapshots ----------------------------------------------------------

    def handle_activity(self, now: float) -> None:
        self._step(self._policy.on_activity, now)

    def _step(self, event: Callable[[float], list[SnapshotRequest]], now: float) -> None:
        """Feed one event to the policy, take its snapshots, and follow up side effects."""
        was_paused = self._policy.state == "paused"
        before = self._policy.next_deadline()
        for req in event(now):
            self._snapshot(req)
        if was_paused and self._policy.state != "paused":
            self._save_state()  # a timed pause expired: a restart must not resurrect it
        after = self._policy.next_deadline()
        if after is not None and (before is None or after < before):
            self._wake.set()

    def _snapshot(self, req: SnapshotRequest) -> None:
        if self._closing:
            return
        # Read the frame now, not when the write runs: a "before" must predate the redraw.
        # Runs inside the activity watcher's device reader: never let a frame-source
        # failure escape into it (the watcher would stop reading that device).
        try:
            frame = self._frames.latest_frame()
        except Exception as exc:
            log.error("snapshot dropped: reading the frame failed: %s",
                      str(exc) or type(exc).__name__)
            return
        if frame is None:
            log.info("snapshot dropped: no frame available")
            return
        self._writes.put_nowait((frame, req))
        if self._write_worker is None or self._write_worker.done():
            self._write_worker = asyncio.create_task(self._write_loop())

    async def _write_loop(self) -> None:
        while True:
            frame, req = await self._writes.get()
            try:
                row = await asyncio.to_thread(self._writer.write, frame, req)
            except Exception as exc:
                log.error("capture write failed: %s", str(exc) or type(exc).__name__)
            else:
                self._record_disk_usage(self._disk_bytes + row.bytes)
            finally:
                self._writes.task_done()

    # -- control -----------------------------------------------------------------------

    async def handle(self, request: dict) -> dict:
        cmd = request.get("cmd")
        if cmd == "status":
            return dict(self.status())
        if cmd == "pause":
            seconds = request.get("seconds")
            if seconds is not None and (
                not isinstance(seconds, (int, float)) or isinstance(seconds, bool)
                or not math.isfinite(seconds) or seconds < 0
            ):
                return {"ok": False, "error": "invalid pause duration"}
            now = self._clock()
            until = None if seconds is None else now + seconds
            for req in self._policy.pause(now, until):
                self._snapshot(req)
            self._save_state()
            self._wake.set()
            return dict(self.status())
        if cmd == "resume":
            self._policy.resume()
            self._save_state()
            self._wake.set()
            return dict(self.status())
        return {"ok": False, "error": f"unknown command: {cmd}"}

    def save_restore_token(self, token: str) -> None:
        """Persist a new portal restore token (the PortalFrameSource callback)."""
        self._state.restore_token = token
        self._save_state()

    def _save_state(self) -> None:
        self._state = State(
            paused=self._policy.state == "paused",
            paused_until=self._policy.paused_until,
            restore_token=self._state.restore_token,
        )
        try:
            self._state_file.save(self._state)
        except OSError as exc:
            log.warning("could not save daemon state: %s", exc.strerror or type(exc).__name__)

    def _record_disk_usage(self, total: int) -> None:
        """Update the cached usage; log once on crossing the warning threshold either way."""
        self._disk_bytes = total
        limit = self._config.disk_warning_gb * 1e9
        if total > limit and not self._disk_warned:
            self._disk_warned = True
            log.warning("capture storage uses %.2f GB, above disk_warning_gb = %g",
                        total / 1e9, self._config.disk_warning_gb)
        elif total <= limit and self._disk_warned:
            self._disk_warned = False
            log.info("capture storage back under disk_warning_gb (%.2f GB)", total / 1e9)

    def status(self) -> Status:
        now = self._clock()
        midnight = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0)
        until = self._policy.paused_until
        disk = self._disk_bytes
        return {
            "ok": True,
            "state": self._policy.state,
            "paused_until": None if until is None else format_ts(until),
            "captures_today": self._store.count_since(format_ts(midnight.timestamp())),
            "last_capture": self._store.last_ts(),
            "disk_bytes": disk,
            "disk_warning": disk > self._config.disk_warning_gb * 1e9,
            "activity": "available" if self._watcher.available else "unavailable",
            "capture": "available" if self._frames.available else "unavailable",
            "paths": {
                "config": str(self._paths.config_file),
                "data": str(self._paths.data_dir),
                "state": str(self._paths.state_file),
            },
        }

    # -- test helpers ------------------------------------------------------------------

    async def tick_once(self) -> None:
        """Test helper: one policy tick at the current clock, then wait for its writes."""
        self._step(self._policy.on_tick, self._clock())
        await self.drain()

    async def drain(self) -> None:
        """Wait until every queued capture write has finished (also used by close())."""
        await self._writes.join()
