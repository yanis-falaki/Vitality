"""Persisted daemon state and the Unix-socket control channel.

Protocol: UTF-8 JSON, one object per line in each direction, one request and one
response per connection; the server closes the connection after replying.
"""

import asyncio
import contextlib
import json
import logging
import os
import re
import stat
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from pathlib import Path

log = logging.getLogger(__name__)

RESPONSE_TIMEOUT = 5.0
_DURATION_RE = re.compile(r"([0-9]+)([smh])")
_DURATION_UNITS = {"s": 1, "m": 60, "h": 3600}


class DaemonNotRunning(RuntimeError):
    def __init__(self) -> None:
        super().__init__("vitality-client daemon is not running")


@dataclass
class State:
    paused: bool = False
    paused_until: float | None = None
    restore_token: str | None = None


def _is_number(v: object) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


class StateFile:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def load(self) -> State:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return State()
        except (OSError, ValueError) as e:
            # Never log file contents: they may include the restore token.
            log.warning("ignoring unreadable state file %s: %s", self.path, type(e).__name__)
            return State()
        paused = data.get("paused", False) if isinstance(data, dict) else None
        until = data.get("paused_until") if isinstance(data, dict) else None
        token = data.get("restore_token") if isinstance(data, dict) else None
        if (not isinstance(data, dict) or not isinstance(paused, bool)
                or not (until is None or _is_number(until))
                or not (token is None or isinstance(token, str))):
            log.warning("ignoring corrupt state file %s", self.path)
            return State()
        return State(paused, None if until is None else float(until), token)

    def save(self, state: State) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=self.path.name + ".")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(asdict(state), f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise


def parse_duration(text: str) -> float:
    m = _DURATION_RE.fullmatch(text)
    if m is None:
        raise ValueError(f"invalid duration: {text}")
    return float(int(m.group(1)) * _DURATION_UNITS[m.group(2)])


Handler = Callable[[dict], Awaitable[dict]]


class ControlServer:
    def __init__(self, socket_path: Path, handler: Handler) -> None:
        self.socket_path = Path(socket_path)
        self._handler = handler
        self._server: asyncio.Server | None = None

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
            await self._server.wait_closed()
            self._server = None
            self.socket_path.unlink(missing_ok=True)

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            try:
                line = await reader.readline()
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
