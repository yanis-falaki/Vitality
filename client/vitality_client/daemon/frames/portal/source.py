"""PortalFrameSource: whole-desktop frames from the screen-capture portal, kept alive.

Combines a `ScreenCastSession` (permission and PipeWire streams, over D-Bus) with
`FrameStreams` (GStreamer pipelines holding the newest frame). Implements the
`FrameSource` interface: `start()`, `latest_frame()`, `available`, `close()`.

If the session cannot be opened, or later breaks, it is torn down and reopened with
backoff (2, 4, 8 ... 60 s). If the user cancels the permission dialog, it stops
retrying. A restore token the portal rejects is retried once without it.

Never logged: image contents and the restore token value.
"""

import asyncio
import logging
import time
from collections.abc import Callable

from PIL import Image

from vitality_client.daemon.frames.interface import FrameSource
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
        restore_token: str | None,
        on_restore_token: Callable[[str], None],
        max_fps: int = 5,
        session_factory: Callable[[], PortalSession] = ScreenCastSession,
    ) -> None:
        self._restore_token = restore_token
        self._on_restore_token = on_restore_token
        self._session_factory = session_factory
        self.available = False

        self._task: asyncio.Task | None = None
        self._first_attempt = asyncio.Event()
        self._session: PortalSession | None = None
        self._streams: FrameStreams = GstFrameStreams(max_fps)

    # -- FrameSource -------------------------------------------------------------------

    async def start(self) -> None:
        """Run the first session attempt (which may wait on the user's portal dialog).

        Returns once that attempt has succeeded or failed; failures and later stream
        errors are retried with backoff in the background until close(), except a
        user-cancelled permission dialog, which stops all further attempts.
        """
        if self._task is None:
            self._first_attempt.clear()
            self._task = asyncio.create_task(self._run())
        try:
            await self._first_attempt.wait()
        except asyncio.CancelledError:
            await self.close()
            raise

    def latest_frame(self) -> Image.Image | None:
        return self._streams.latest_frame()

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
                log.info("screen capture started (%d stream(s))", len(self._streams))
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
        token = self._restore_token
        try:
            streams = await self._open_session(token)
        except PortalResponseError as exc:
            if token is None or exc.response == RESPONSE_CANCELLED:
                raise
            log.info("portal rejected the saved restore token; asking again without it")
            self._restore_token = None
            await self._teardown()
            streams = await self._open_session(None)
        fd = self._session.fd if self._session else None
        if fd is None:
            raise PortalError("portal session has no PipeWire connection")
        self._streams.start(fd, streams)

    async def _open_session(self, token: str | None) -> list[Stream]:
        self._session = self._session_factory()
        return await self._session.open(token, self._save_restore_token)

    def _save_restore_token(self, token: str) -> None:
        self._restore_token = token
        try:
            self._on_restore_token(token)
        except Exception as exc:
            log.warning("could not save the portal restore token: %s", _describe(exc))

    async def _watch(self) -> str:
        """Wait until a pipeline fails or the portal session goes away; return why."""
        while True:
            reason = self._streams.failure() or (
                self._session.lost() if self._session else "portal session missing")
            if reason:
                return reason
            await asyncio.sleep(_WATCH_INTERVAL)

    async def _teardown(self) -> None:
        self.available = False
        self._streams.stop()
        session, self._session = self._session, None
        if session is not None:
            await session.close()


def _describe(exc: BaseException) -> str:
    return str(exc) or type(exc).__name__
