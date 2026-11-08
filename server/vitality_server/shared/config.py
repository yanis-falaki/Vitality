"""The server's settings, read from environment variables.

Variable                  Default   Meaning
SERVER_DATA_DIR           /data     Directory holding captures/ and tmp/. If set,
                                    it must not be empty.
SERVER_PORT               8100      TCP port to listen on, an integer from 1 to 65535. Empty
                                    means the default.
SERVER_ALLOWED_CLIENTS    (none)    Comma-separated Tailscale device names allowed to upload.
                                    Whitespace around names and empty names are dropped; empty
                                    means no client is allowed.

A variable that is unset keeps the default of `ServerConfig`. Other variables are ignored.
"""

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ServerConfig:
    data_dir: Path = Path("/data")              # holds captures/ and tmp/
    port: int = 8100
    allowed_clients: tuple[str, ...] = ()       # Tailscale device names allowed to upload


def load_server_config(environ: Mapping[str, str] = os.environ) -> ServerConfig:
    """Read the settings from `environ` (the process environment by default).

    Raises ValueError, naming the variable, if `SERVER_DATA_DIR` is set but empty or
    `SERVER_PORT` is not an integer from 1 to 65535.
    """
    values: dict[str, Path | int | tuple[str, ...]] = {}

    data_dir = environ.get("SERVER_DATA_DIR")
    if data_dir is not None:
        if not data_dir:
            raise ValueError("SERVER_DATA_DIR must be a non-empty path, got ''")
        values["data_dir"] = Path(data_dir)

    port = environ.get("SERVER_PORT", "").strip()
    if port:
        if not re.fullmatch(r"[0-9]+", port) or not 1 <= int(port) <= 65535:
            raise ValueError(f"SERVER_PORT must be an integer from 1 to 65535, got {port!r}")
        values["port"] = int(port)

    clients = [name.strip() for name in environ.get("SERVER_ALLOWED_CLIENTS", "").split(",")]
    values["allowed_clients"] = tuple(name for name in clients if name)

    return ServerConfig(**values)  # pyright: ignore[reportArgumentType]  # types validated above
