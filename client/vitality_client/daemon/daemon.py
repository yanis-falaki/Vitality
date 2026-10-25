"""The running daemon: records interactions — screenshots around bursts of user activity.

The contract (`Daemon`) comes first; the implementation (`CaptureDaemon`) follows.
The messages it answers (`Request`, `Reply`, `Status`) are defined in shared/channel.py,
since the CLI uses them too. CaptureDaemon wires the packages together: activity → policy →
frame → writer (an interaction's shots, then its end) → uploader, plus a timer loop and the
control socket. Everything runs on one asyncio loop; image encoding and disk writes run in
a worker thread, one at a time, in request order.
"""

import asyncio
import contextlib
import logging
import math
import signal
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from PIL import Image

from vitality_client.daemon.activity import ActivitySource
from vitality_client.shared.config import Config, Paths
from vitality_client.shared.channel import (
    ControlServer,
    Reply,
    Request,
    Status,
    UnixControlServer,
    UploadStatus,
)
from vitality_client.shared.state import State, StateFile, StateStore
from vitality_client.daemon.frames import FrameSource
from vitality_client.daemon.policy import (
    Action,
    CapturePolicy,
    InteractionEnd,
    Snapshot,
    SnapshotPolicy,
)
from vitality_client.daemon.storage import (
    CaptureIndex,
    CaptureWriter,
    ShotRow,
    Store,
    Writer,
    format_ts,
    local_tz_name,
)
from vitality_client.daemon.upload import Uploader


# == Contract =============================================================================

class Daemon(Protocol):
    """Records interactions (screenshots around bursts of user activity) until stopped.

    It is built from a frame source, an activity source and, optionally, an uploader
    factory (given the capture index); it creates its own capture policy, writer and
    control server. When it runs, it hands each part a callback or drives it:

        activity.run(self.handle_activity)      input happened
        server.start(self.handle)               a CLI request arrived
        frames.start(self.save_restore_token)   a screen-sharing token to keep
        uploader.run()                          upload ended interactions in the background
        uploader.wake()                         called when an interaction has ended
    """

    async def run(self) -> None:
        """Start everything, run until SIGTERM/SIGINT, then shut down cleanly."""
        ...

    async def close(self) -> None:
        """Stop taking snapshots, finish pending writes, release everything."""
        ...

    def handle_activity(self, now: float) -> None:
        """Input happened at `now`: may open an interaction with its first shot, or extend
        the open one."""
        ...

    async def handle(self, request: Request) -> Reply:
        """Answer one CLI request: `status`, `pause` and `resume` reply with the `Status`;
        `capture` takes one screenshot now (even while paused) and replies with its path:
        it joins the open interaction, or is an interaction of its own."""
        ...

    def save_restore_token(self, token: str) -> None:
        """Keep a new screen-sharing permission token for the next start."""
        ...

    def status(self) -> Status:
        """What the daemon is doing right now."""
        ...


# == Implementation =======================================================================

log = logging.getLogger(__name__)

DRAIN_TIMEOUT = 10.0
CAPTURE_TIMEOUT = 4.0  # under the CLI's 5 s response timeout


@dataclass(frozen=True)
class _Shot:
    """Write queue item: save one screenshot of the interaction opened at `start`."""

    frame: Image.Image
    start: float
    ts: float
    trigger: Literal["activity", "manual"]
    saved: asyncio.Future[ShotRow] | None = None  # receives the saved row, for `capture`


@dataclass(frozen=True)
class _End:
    """Write queue item: close the interaction opened at `start` (after its shots)."""

    start: float


class CaptureDaemon(Daemon):
    def __init__(
        self,
        config: Config,
        paths: Paths,
        frames: FrameSource,
        activity: ActivitySource,
        clock: Callable[[], float] = time.time,
        uploader: Callable[[CaptureIndex], Uploader] | None = None,
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
            config.settle_seconds, config.max_gap_seconds,
            paused=self._state.paused, paused_until=self._state.paused_until,
        )
        self._store: CaptureIndex = Store(paths.data_dir / "index.sqlite")
        self._writer: CaptureWriter = Writer(
            paths.data_dir, self._store, config.webp_quality, tz=local_tz_name())
        self._server: ControlServer = UnixControlServer(paths.socket_path)
        self._watcher = activity
        self._uploader: Uploader | None = None if uploader is None else uploader(self._store)
        # Pending disk work, in order: shots, and each interaction's end after its shots.
        self._writes: asyncio.Queue[_Shot | _End] = asyncio.Queue()
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
            await self._server.start(self.handle)
            removed = self._writer.cleanup_orphans()
            if removed:
                log.info("removed %d orphaned capture file(s)", removed)
            closed = self._writer.close_interrupted()
            if closed:
                log.info("closed %d interrupted interaction(s)", closed)
            # Before any task starts, so no write can land mid-scan and be counted twice.
            self._record_disk_usage(await asyncio.to_thread(self._writer.disk_usage_bytes))
            critical = {
                asyncio.create_task(self._watcher.run(self.handle_activity)),
                asyncio.create_task(self._tick_loop()),
            }
            # Not critical: frames.start() may wait on the portal permission dialog, and an
            # uploader crash must not stop capture. Both log their own errors.
            tasks = [asyncio.create_task(self._start_frames()), *critical]
            if self._uploader is not None:
                tasks.append(asyncio.create_task(self._run_uploader(self._uploader)))
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
            await self._frames.start(self.save_restore_token)
        except Exception as exc:
            log.error("screen capture failed to start: %s", str(exc) or type(exc).__name__)

    async def _run_uploader(self, uploader: Uploader) -> None:
        try:
            await uploader.run()
        except Exception as exc:
            log.error("the uploader stopped: %s", str(exc) or type(exc).__name__)

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

    def _step(self, event: Callable[[float], list[Action]], now: float) -> None:
        """Feed one event to the policy, carry out its actions, and follow up side effects."""
        was_paused = self._policy.state == "paused"
        before = self._policy.next_deadline()
        self._apply(event(now))
        if was_paused and self._policy.state != "paused":
            self._save_state()  # a timed pause expired: a restart must not resurrect it
        after = self._policy.next_deadline()
        if after is not None and (before is None or after < before):
            self._wake.set()

    def _apply(self, actions: list[Action]) -> None:
        for action in actions:
            if isinstance(action, Snapshot):
                self._snapshot(action)
            elif not self._closing:  # an interaction left open is closed at the next start
                self._enqueue(_End(action.interaction.start))

    def _snapshot(self, snap: Snapshot) -> None:
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
        self._enqueue(_Shot(frame, snap.interaction.start, snap.ts, snap.trigger))

    def _enqueue(self, item: _Shot | _End) -> None:
        self._writes.put_nowait(item)
        if self._write_worker is None or self._write_worker.done():
            self._write_worker = asyncio.create_task(self._write_loop())

    async def _write_loop(self) -> None:
        while True:
            item = await self._writes.get()
            try:
                if isinstance(item, _Shot):
                    await self._write_shot(item)
                else:
                    await self._end_interaction(item)
            finally:
                self._writes.task_done()

    async def _write_shot(self, shot: _Shot) -> None:
        saved = shot.saved
        try:
            row = await asyncio.to_thread(
                self._writer.write, shot.frame, shot.start, shot.ts, shot.trigger)
        except Exception as exc:
            log.error("capture write failed: %s", str(exc) or type(exc).__name__)
            if saved is not None and not saved.done():
                saved.set_exception(exc)
        else:
            self._record_disk_usage(self._disk_bytes + row.bytes)
            if saved is not None and not saved.done():
                saved.set_result(row)

    async def _end_interaction(self, end: _End) -> None:
        try:
            name = await asyncio.to_thread(self._writer.end, end.start)
        except Exception as exc:
            log.error("closing the interaction failed: %s", str(exc) or type(exc).__name__)
            return
        if name is not None and self._uploader is not None:
            self._uploader.wake()

    # -- control -----------------------------------------------------------------------

    async def handle(self, request: Request) -> Reply:
        cmd = request.get("cmd")  # the socket passes on any JSON; unknown commands get an error
        if cmd == "status":
            return self.status()
        if cmd == "pause":
            seconds = request.get("seconds")
            if seconds is not None and (
                not isinstance(seconds, (int, float)) or isinstance(seconds, bool)
                or not math.isfinite(seconds) or seconds < 0
            ):
                return {"ok": False, "error": "invalid pause duration"}
            now = self._clock()
            until = None if seconds is None else now + seconds
            self._apply(self._policy.pause(now, until))
            self._save_state()
            self._wake.set()
            return self.status()
        if cmd == "resume":
            self._policy.resume()
            self._save_state()
            self._wake.set()
            return self.status()
        if cmd == "capture":
            return await self._capture_now()
        return {"ok": False, "error": f"unknown command: {cmd}"}

    async def _capture_now(self) -> Reply:
        """One screenshot on request: saved like any other, its path returned.

        It joins the open interaction, or (idle or paused) is a one-shot interaction."""
        if self._closing:
            return {"ok": False, "error": "the daemon is shutting down"}
        try:
            frame = self._frames.latest_frame()
        except Exception as exc:
            return {"ok": False, "error": f"reading the frame failed: {str(exc) or type(exc).__name__}"}
        if frame is None:
            return {"ok": False, "error": "no screen frame available yet"}
        saved: asyncio.Future[ShotRow] = asyncio.get_running_loop().create_future()
        for action in self._policy.manual(self._clock()):
            if isinstance(action, Snapshot):
                self._enqueue(_Shot(frame, action.interaction.start, action.ts, action.trigger,
                                    saved))
            elif isinstance(action, InteractionEnd):
                self._enqueue(_End(action.interaction.start))
        try:
            row = await asyncio.wait_for(asyncio.shield(saved), CAPTURE_TIMEOUT)
        except TimeoutError:
            return {"ok": False, "error": "the capture is taking too long; check the log"}
        except Exception as exc:
            return {"ok": False, "error": f"capture failed: {str(exc) or type(exc).__name__}"}
        return {"ok": True, "path": str((self._paths.data_dir / row.path).resolve())}

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
            "captures_today": self._store.count_shots_since(format_ts(midnight.timestamp())),
            "last_capture": self._store.last_shot_ts(),
            "disk_bytes": disk,
            "disk_warning": disk > self._config.disk_warning_gb * 1e9,
            "activity": "available" if self._watcher.available else "unavailable",
            "capture": "available" if self._frames.available else "unavailable",
            "paths": {
                "config": str(self._paths.config_file),
                "data": str(self._paths.data_dir),
                "state": str(self._paths.state_file),
            },
            "upload": self._upload_status(),
        }

    def _upload_status(self) -> UploadStatus:
        if self._uploader is not None:
            return self._uploader.status
        return {"state": "disabled", "pending": self._store.pending_count(), "last_upload": None}

    # -- test helpers ------------------------------------------------------------------

    async def tick_once(self) -> None:
        """Test helper: one policy tick at the current clock, then wait for its writes."""
        self._step(self._policy.on_tick, self._clock())
        await self.drain()

    async def drain(self) -> None:
        """Wait until every queued write and end has finished (also used by close())."""
        await self._writes.join()
