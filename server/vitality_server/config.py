"""The server's settings, read from a TOML file.

Recognised keys: `data_dir`, `port`, `allowed_clients`. Anything omitted keeps the
default of `ServerConfig`.
"""

import tomllib
from dataclasses import dataclass
from pathlib import Path


# == Contract =============================================================================

@dataclass(frozen=True)
class ServerConfig:
    data_dir: Path = Path("/srv/vitality")      # holds captures/, tmp/ and index.sqlite
    port: int = 8100
    allowed_clients: tuple[str, ...] = ()       # Tailscale device names allowed to upload


def load_server_config(path: Path) -> ServerConfig:
    """Load settings from a TOML file; a missing file yields the defaults.

    `data_dir` must be a non-empty string, `port` an integer from 1 to 65535 and
    `allowed_clients` a list of strings.

    Raises ValueError, naming the key, for an unknown key or a value of the wrong type or
    range. A file that is not valid TOML raises `tomllib.TOMLDecodeError` (a ValueError); one
    that cannot be read raises OSError.
    """
    try:
        with open(path, "rb") as f:
            raw = tomllib.load(f)
    except FileNotFoundError:
        return ServerConfig()

    unknown = [key for key in raw if key not in ("data_dir", "port", "allowed_clients")]
    if unknown:
        raise ValueError(f"unknown config key: {unknown[0]}")

    values: dict[str, Path | int | tuple[str, ...]] = {}
    if "data_dir" in raw:
        data_dir = raw["data_dir"]
        if not isinstance(data_dir, str) or not data_dir:
            raise ValueError(f"config key data_dir must be a non-empty path, got {data_dir!r}")
        values["data_dir"] = Path(data_dir)
    if "port" in raw:
        port = raw["port"]
        if not isinstance(port, int) or isinstance(port, bool):
            raise ValueError(f"config key port must be an integer, got {port!r}")
        if not 1 <= port <= 65535:
            raise ValueError(f"config key port must be between 1 and 65535, got {port!r}")
        values["port"] = port
    if "allowed_clients" in raw:
        clients = raw["allowed_clients"]
        if not isinstance(clients, list) or not all(isinstance(c, str) for c in clients):
            raise ValueError(f"config key allowed_clients must be a list of strings, got {clients!r}")
        values["allowed_clients"] = tuple(clients)
    return ServerConfig(**values)  # pyright: ignore[reportArgumentType]  # types validated above
