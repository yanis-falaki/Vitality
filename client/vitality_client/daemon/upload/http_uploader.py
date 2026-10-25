"""Uploading ended interactions to the server over HTTP.

The loop (`run`), until cancelled: `upload_once()`, then wait before the next round:

    uploaded, failed   no wait (look at the next pending interaction); resets the backoff
    offline            the current backoff, in full: 5 s, doubling to 300 s
    rejected           `rejected_wait` (300 s), in full
    idle               `idle_wait` (60 s), or until `wake()` — whichever comes first

Only the idle wait is cut short by `wake()` (an interaction just ended). Retrying during an
outage or after a 403 keeps its own pace however often interactions end; a `wake()` that
arrives meanwhile is not lost, as the next round looks at everything pending anyway.

`upload_once()` sends the oldest pending interaction: one `POST /interactions` with the
interaction's JSON and its shot files. What the server's answer means (spec section 6.4):

    200, 201                              uploaded    mark the interaction uploaded
    403                                   rejected    keep it pending; this device is not allowed
    409, 413, 422                         failed      mark it failed (skipped for good), log it
    not buildable from the index, or a
      local file missing or unreadable    failed      the same, without sending anything
    connection error, timeout, any other  offline     keep it pending, retry after the backoff

Any other error in a round (a bad URL, the index failing, a bug) is logged with its
traceback and treated as offline: the loop backs off and tries again, and never ends on
its own; only cancellation stops it.

Pausing the daemon does not stop uploads: they are about what is already recorded.
Logging: one line per state change (ok / offline / rejected), never per retry, plus an
error line for each failed interaction. File contents are never logged.
"""

import asyncio
import contextlib
import logging
import time
from pathlib import Path
from typing import Literal

import httpx
from pydantic import ValidationError
from vitality_protocol.messages import ErrorReply
from vitality_protocol.routes import INTERACTION_FIELD, INTERACTIONS_PATH, SHOTS_FIELD

from vitality_client.daemon.storage import CaptureIndex, format_ts
from vitality_client.daemon.upload.interface import Uploader
from vitality_client.shared.channel import UploadStatus

log = logging.getLogger(__name__)

REQUEST_TIMEOUT = 30.0  # seconds, for one upload
Outcome = Literal["uploaded", "idle", "offline", "rejected", "failed"]
State = Literal["ok", "offline", "rejected"]


class HttpUploader(Uploader):
    def __init__(
        self,
        server: str,
        index: CaptureIndex,
        data_dir: Path,
        client: httpx.AsyncClient | None = None,
        retry_initial: float = 5.0,
        retry_max: float = 300.0,
        rejected_wait: float = 300.0,
        idle_wait: float = 60.0,
    ) -> None:
        self._server = server
        self._index = index
        self._data_dir = data_dir
        self._client = client
        self._owns_client = client is None  # one we create, we close
        self._retry_initial = retry_initial
        self._retry_max = retry_max
        self._rejected_wait = rejected_wait
        self._idle_wait = idle_wait
        self._wake = asyncio.Event()
        self._state: State = "ok"
        self._last_upload = index.last_uploaded_at()  # so status survives a restart

    # -- Uploader ------------------------------------------------------------------------

    async def run(self) -> None:
        """Upload until cancelled. Only the idle wait ends early on `wake()`; the offline
        backoff and the rejected wait always run in full (see the module docstring). An
        unexpected error in a round is logged and counts as offline; it never ends the loop."""
        backoff = self._retry_initial
        try:
            while True:
                self._wake.clear()  # before the upload, so a wake() during it is not lost
                try:
                    outcome = await self.upload_once()
                except Exception as exc:  # not CancelledError: that one stops the loop
                    log.exception("upload round failed")
                    self._set_state("offline", str(exc) or type(exc).__name__)
                    outcome = "offline"
                if outcome in ("uploaded", "failed"):  # the server answered
                    backoff = self._retry_initial
                elif outcome == "offline":
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, self._retry_max)
                elif outcome == "rejected":
                    await asyncio.sleep(self._rejected_wait)
                else:
                    with contextlib.suppress(TimeoutError):
                        async with asyncio.timeout(self._idle_wait):
                            await self._wake.wait()
        finally:
            if self._owns_client and self._client is not None:
                client, self._client = self._client, None
                await client.aclose()

    def wake(self) -> None:
        self._wake.set()

    @property
    def status(self) -> UploadStatus:
        return {
            "state": self._state,
            "pending": self._index.pending_count(),
            "last_upload": self._last_upload,
        }

    # -- one upload ----------------------------------------------------------------------

    async def upload_once(self) -> Outcome:
        """Send the oldest pending interaction and say what came of it."""
        pending = self._index.pending_uploads()
        if not pending:
            return "idle"
        name = pending[0]
        try:
            interaction = self._index.interaction(name)
        except (KeyError, ValidationError) as exc:
            return self._fail(name, f"it cannot be read from the index: {_describe(exc)}")
        try:
            shots = [await asyncio.to_thread((self._data_dir / "captures" / name / shot.file).read_bytes)
                     for shot in interaction.shots]
        except OSError as exc:
            return self._fail(name, f"cannot read a screenshot: {exc.strerror or type(exc).__name__}")
        files = [(SHOTS_FIELD, (shot.file, content, "image/webp"))
                 for shot, content in zip(interaction.shots, shots)]
        try:
            response = await self._http().post(
                INTERACTIONS_PATH,
                data={INTERACTION_FIELD: interaction.model_dump_json()},
                files=files,
                timeout=REQUEST_TIMEOUT,
            )
        except httpx.HTTPError as exc:  # no route, refused, timed out, dropped
            self._set_state("offline", str(exc) or type(exc).__name__)
            return "offline"
        code = response.status_code
        if code in (200, 201):
            self._last_upload = format_ts(time.time())
            self._index.mark_uploaded(name, self._last_upload)
            self._set_state("ok", "upload succeeded")
            return "uploaded"
        if code == 403:
            self._set_state("rejected", _reason(response))
            return "rejected"
        if code in (409, 413, 422):
            return self._fail(name, f"the server refused it (HTTP {code}): {_reason(response)}")
        self._set_state("offline", f"HTTP {code}: {_reason(response)}")
        return "offline"

    # -- helpers -------------------------------------------------------------------------

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(base_url=self._server)
        return self._client

    def _fail(self, name: str, reason: str) -> Literal["failed"]:
        self._index.mark_failed(name, reason)
        log.error("interaction %s will not be uploaded: %s", name, reason)
        return "failed"

    def _set_state(self, new: State, reason: str) -> None:
        if new == self._state:
            return
        log.log(logging.INFO if new == "ok" else logging.WARNING,
                "upload: %s -> %s (%s)", self._state, new, reason)
        self._state = new


def _describe(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return f"{exc.error_count()} invalid field(s): " + "; ".join(
            f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
    return f"{type(exc).__name__}: {exc}"


def _reason(response: httpx.Response) -> str:
    """The server's error message, or just the status when it did not send one."""
    try:
        return ErrorReply.model_validate_json(response.content).error
    except ValidationError:
        return f"HTTP {response.status_code}"
