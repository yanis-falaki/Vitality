"""The Unix-socket channel between the CLI and the running daemon.

Protocol: UTF-8 JSON, one object per line in each direction, one request and one
response per connection; the server closes the connection after replying.
"""

import asyncio
import contextlib
import json
import logging
import os
import stat
from collections.abc import Awaitable, Callable
from pathlib import Path

log = logging.getLogger(__name__)

RESPONSE_TIMEOUT = 5.0
REQUEST_TIMEOUT = 5.0


class DaemonNotRunning(RuntimeError):
    def __init__(self) -> None:
        super().__init__("vitality-client daemon is not running")


Handler = Callable[[dict], Awaitable[dict]]


class ControlServer:
    def __init__(self, socket_path: Path, handler: Handler) -> None:
        self.socket_path = Path(socket_path)
        self._handler = handler
        self._server: asyncio.Server | None = None
        self._writers: set[asyncio.StreamWriter] = set()

    async def start(self) -> None:
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
                response = {"ok": False, "error": "invalid request"}
            else:
                try:
                    response = await self._handler(request)
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


async def send_command(socket_path: Path, request: dict) -> dict:
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
    return json.loads(line)
