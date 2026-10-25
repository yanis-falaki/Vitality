"""The Unix-socket channel between the CLI and the running daemon.

Wire format: UTF-8 JSON, one object per line in each direction, one request and one
response per connection; the server closes the connection after replying.

The contract comes first: every request and reply that crosses the channel, the
`Handler` the daemon provides, `ControlServer` (the daemon's end) and `DaemonNotRunning`.
The implementation (`UnixControlServer`, and `send_command` — the CLI's end) follows.
"""

import asyncio
import contextlib
import json
import logging
import os
import stat
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Literal, NotRequired, Protocol, TypedDict, cast


# == Contract =============================================================================

# -- requests: what the CLI can ask ------------------------------------------------------

class StatusRequest(TypedDict):
    cmd: Literal["status"]


class PauseRequest(TypedDict):
    cmd: Literal["pause"]
    seconds: NotRequired[float | None]  # omitted or None: until resumed


class ResumeRequest(TypedDict):
    cmd: Literal["resume"]


class CaptureRequest(TypedDict):
    cmd: Literal["capture"]  # take one screenshot now, even while paused


Request = StatusRequest | PauseRequest | ResumeRequest | CaptureRequest


# -- replies: what the daemon answers -----------------------------------------------------

class StatusPaths(TypedDict):
    config: str  # client.toml
    data: str    # captures/ and index.sqlite
    state: str   # state.json


class UploadStatus(TypedDict):
    """How sending ended interactions to the server is going."""

    state: Literal["disabled", "ok", "offline", "rejected"]
    pending: int                     # ended interactions not yet uploaded
    last_upload: str | None          # UTC ISO timestamp


class Status(TypedDict):
    """The reply to `status`, `pause` and `resume`: what the daemon is doing now."""

    ok: Literal[True]
    state: Literal["idle", "active", "paused"]
    paused_until: str | None   # UTC ISO timestamp when a timed pause ends
    captures_today: int        # screenshots since local midnight
    last_capture: str | None   # UTC ISO timestamp of the newest screenshot
    disk_bytes: int            # total size of saved captures
    disk_warning: bool         # above disk_warning_gb
    activity: Literal["available", "unavailable"]  # can input be read?
    capture: Literal["available", "unavailable"]   # is the screen stream up?
    paths: StatusPaths
    upload: UploadStatus


class CaptureReply(TypedDict):
    """The reply to `capture`."""

    ok: Literal[True]
    path: str  # absolute path of the saved screenshot


class ErrorReply(TypedDict):
    """The reply to any request that could not be done."""

    ok: Literal[False]
    error: str


Reply = Status | CaptureReply | ErrorReply


# -- the two ends -------------------------------------------------------------------------

Handler = Callable[[Request], Awaitable[Reply]]
"""Answers one request (the daemon's `handle`)."""


class DaemonNotRunning(RuntimeError):
    """Raised by `send_command` when nothing answers on the socket."""

    def __init__(self) -> None:
        super().__init__("vitality-client daemon is not running")


class ControlServer(Protocol):
    """The daemon's end of the channel: answers each request on the socket with its handler.

    The CLI's end is `send_command(socket_path, request) -> dict`.
    """

    async def start(self, handler: Handler) -> None:
        """Create the socket and answer every request on it by calling `handler(request)`.

        Raises RuntimeError if another daemon is already answering on it; a stale socket
        left by a crash is replaced.
        """
        ...

    async def close(self) -> None:
        """Stop answering and remove the socket."""
        ...


# == Implementation =======================================================================

log = logging.getLogger(__name__)

RESPONSE_TIMEOUT = 5.0
REQUEST_TIMEOUT = 5.0


class UnixControlServer(ControlServer):
    def __init__(self, socket_path: Path) -> None:
        self.socket_path = Path(socket_path)
        self._handler: Handler | None = None
        self._server: asyncio.Server | None = None
        self._writers: set[asyncio.StreamWriter] = set()

    async def start(self, handler: Handler) -> None:
        self._handler = handler
        self.socket_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        await self._clear_stale_socket()
        self._server = await asyncio.start_unix_server(self._serve, path=str(self.socket_path))
        os.chmod(self.socket_path, 0o600)

    async def _clear_stale_socket(self) -> None:
        try:
            mode = self.socket_path.lstat().st_mode
        except FileNotFoundError:
            return
        if not stat.S_ISSOCK(mode):
            raise RuntimeError(f"{self.socket_path} exists and is not a socket")
        try:
            _, writer = await asyncio.open_unix_connection(str(self.socket_path))
        except (ConnectionRefusedError, FileNotFoundError):
            self.socket_path.unlink(missing_ok=True)
            return
        writer.close()
        await writer.wait_closed()
        raise RuntimeError("vitality-client daemon is already running")

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            # wait_closed() waits for active connections; a stuck client must not block it.
            for writer in list(self._writers):
                writer.transport.abort()
            await self._server.wait_closed()
            self._server = None
            self.socket_path.unlink(missing_ok=True)

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._writers.add(writer)
        try:
            try:
                async with asyncio.timeout(REQUEST_TIMEOUT):
                    line = await reader.readline()
            except TimeoutError:
                return  # client never sent a request: drop it without a response
            except (ValueError, asyncio.LimitOverrunError):
                line = b"\x00"  # oversized line: reported as invalid below
            if not line:
                return  # connected and left without a request (e.g. a liveness probe)
            try:
                request = json.loads(line)
                if not isinstance(request, dict):
                    raise ValueError("request is not an object")
            except ValueError:
                response: Reply | dict = {"ok": False, "error": "invalid request"}
            else:
                try:
                    assert self._handler is not None  # set by start() before serving
                    # Unknown commands reach the handler too; it answers them with an ErrorReply.
                    response = await self._handler(cast(Request, request))
                except Exception as e:
                    log.warning("control handler failed: %s", type(e).__name__)
                    response = {"ok": False, "error": str(e)}
            writer.write(json.dumps(response).encode("utf-8") + b"\n")
            await writer.drain()
        except ConnectionError:
            pass
        finally:
            self._writers.discard(writer)
            writer.close()


async def send_command(socket_path: Path, request: Request) -> Reply:
    try:
        reader, writer = await asyncio.open_unix_connection(str(socket_path))
    except (FileNotFoundError, ConnectionRefusedError, NotADirectoryError):
        raise DaemonNotRunning from None
    try:
        async with asyncio.timeout(RESPONSE_TIMEOUT):
            writer.write(json.dumps(request).encode("utf-8") + b"\n")
            await writer.drain()
            line = await reader.readline()
    except (TimeoutError, ConnectionError):
        raise DaemonNotRunning from None
    finally:
        writer.close()
        with contextlib.suppress(OSError):
            await writer.wait_closed()
    if not line:
        raise RuntimeError("vitality-client daemon closed the connection without replying")
    return cast(Reply, json.loads(line))
