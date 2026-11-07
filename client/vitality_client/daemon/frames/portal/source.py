"""Implements `FrameSource` (see interface.py).

PortalFrameSource: frames of every monitor from the screen-capture portal, kept alive.
`PortalFrameSource` has no public methods beyond the Protocol's. The module's other public
functions are the retry rules, `backoff_delay(failures)` and `should_retry(exc)`.

Combines `sessions` `ScreenCastSession`s (permission and PipeWire streams, over D-Bus),
opened one after the other, with `FrameStreams` (GStreamer pipelines holding the newest
frame). Some portals (Hyprland's) share one monitor per session, others (GNOME, KDE) can
share several in one: every stream of every session is one of the machine's screens.
Each session has its own restore token.

If a session cannot be opened, or one later breaks, all are torn down and reopened with
backoff (2, 4, 8 ... 60 s). If the user cancels a permission dialog, it stops
retrying. A restore token the portal rejects is retried once without it.

Never logged: image contents and the restore token value.
"""

import asyncio
import logging
import time
from collections.abc import Callable, Sequence

from vitality_client.daemon.frames.interface import FrameSource, ScreenFrame
from vitality_client.daemon.frames.portal.session import (
    RESPONSE_CANCELLED,
    PortalError,
    PortalResponseError,
    PortalSession,
    ScreenCastSession,
    Stream,
)
from vitality_client.daemon.frames.portal.stream import FrameStreams, GstFrameStreams

log = logging.getLogger(__name__)

_WATCH_INTERVAL = 0.5
_STABLE_SESSION_SECONDS = 60  # a session that ran this long resets the retry backoff


def backoff_delay(failures: int) -> float:
    """Seconds to wait after the n-th consecutive failure: 2, 4, 8, ... capped at 60."""
    return min(2 ** failures, 60)


def should_retry(exc: Exception) -> bool:
    """False when the user cancelled the permission dialog; every other failure retries."""
    return not (isinstance(exc, PortalResponseError) and exc.response == RESPONSE_CANCELLED)


class PortalFrameSource(FrameSource):
    def __init__(
        self,
        restore_tokens: Sequence[str | None],
        sessions: int = 1,
        max_fps: int = 5,
        session_factory: Callable[[], PortalSession] = ScreenCastSession,
        streams: Callable[[int], FrameStreams] = GstFrameStreams,
    ) -> None:
        """`restore_tokens[i]` is session i's saved token (missing ones count as None)."""
        self._restore_tokens = [*restore_tokens[:sessions],
                                *[None] * (sessions - len(restore_tokens))]
        self._on_restore_token: Callable[[int, str], None] = lambda i, token: None  # by start()
        self._session_factory = session_factory
        self.available = False

        self._task: asyncio.Task | None = None
        self._first_attempt = asyncio.Event()
        self._sessions: list[PortalSession] = []
        self._streams: FrameStreams = streams(max_fps)

    # -- FrameSource -------------------------------------------------------------------

    @property
    def screens(self) -> int:
        return len(self._streams) if self.available else 0

    async def start(self, on_restore_token: Callable[[int, str], None]) -> None:
        """Run the first attempt to open every session (each may wait on a portal dialog).

        Returns once that attempt has succeeded or failed; failures and later stream
        errors are retried with backoff in the background until close(), except a
        user-cancelled permission dialog, which stops all further attempts.
        """
        self._on_restore_token = on_restore_token
        if self._task is None:
            self._first_attempt.clear()
            self._task = asyncio.create_task(self._run())
        try:
            await self._first_attempt.wait()
        except asyncio.CancelledError:
            await self.close()
            raise

    def latest_frames(self) -> list[ScreenFrame] | None:
        return self._streams.latest_frames()

    async def close(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await self._teardown()

    # -- supervision -------------------------------------------------------------------

    async def _run(self) -> None:
        failures = 0
        try:
            while True:
                try:
                    await self._open()
                except Exception as exc:
                    await self._teardown()
                    self._first_attempt.set()
                    if not should_retry(exc):
                        log.warning(
                            "screen capture permission was cancelled; "
                            "restart the daemon to be asked again"
                        )
                        return
                    log.warning("screen capture unavailable: %s", _describe(exc))
                    failures += 1
                    await asyncio.sleep(backoff_delay(failures))
                    continue

                self.available = True
                self._first_attempt.set()
                started = time.monotonic()
                log.info("screen capture started (%d screen(s) from %d session(s))",
                         len(self._streams), len(self._sessions))
                reason = await self._watch()
                log.warning("screen capture stopped: %s", reason)
                self.available = False
                await self._teardown()
                if time.monotonic() - started >= _STABLE_SESSION_SECONDS:
                    failures = 0
                failures += 1
                await asyncio.sleep(backoff_delay(failures))
        finally:
            self.available = False
            self._first_attempt.set()
            await self._teardown()

    async def _open(self) -> None:
        """Open the sessions one after the other (so their dialogs never overlap)."""
        for index in range(len(self._restore_tokens)):
            streams = await self._open_one(index)
            fd = self._sessions[-1].fd
            if fd is None:
                raise PortalError("portal session has no PipeWire connection")
            self._streams.start(fd, streams)

    async def _open_one(self, index: int) -> list[Stream]:
        token = self._restore_tokens[index]
        try:
            return await self._open_session(index, token)
        except PortalResponseError as exc:
            if token is None or exc.response == RESPONSE_CANCELLED:
                raise
            log.info("portal rejected the saved restore token of session %d; "
                     "asking again without it", index + 1)
            self._restore_tokens[index] = None
            await self._sessions.pop().close()
            return await self._open_session(index, None)

    async def _open_session(self, index: int, token: str | None) -> list[Stream]:
        session = self._session_factory()
        self._sessions.append(session)
        return await session.open(token, lambda new: self._save_restore_token(index, new))

    def _save_restore_token(self, index: int, token: str) -> None:
        self._restore_tokens[index] = token
        try:
            self._on_restore_token(index, token)
        except Exception as exc:
            log.warning("could not save the portal restore token: %s", _describe(exc))

    async def _watch(self) -> str:
        """Wait until a pipeline fails or a portal session goes away; return why."""
        while True:
            reason = self._streams.failure() or next(
                (lost for lost in (s.lost() for s in self._sessions) if lost), None,
            ) or (None if self._sessions else "portal session missing")
            if reason:
                return reason
            await asyncio.sleep(_WATCH_INTERVAL)

    async def _teardown(self) -> None:
        self.available = False
        self._streams.stop()
        sessions, self._sessions = self._sessions, []
        for session in sessions:
            await session.close()


def _describe(exc: BaseException) -> str:
    return str(exc) or type(exc).__name__
