"""Helpers shared by the command modules: paths, messages, formatting."""

import asyncio
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

from vitality_client.shared.config import Paths, resolve_paths
from vitality_client.shared.channel import DaemonNotRunning, StatusPaths, send_command


NOT_RUNNING = "vitality-client daemon is not running"
"""What a command prints when the daemon does not answer."""

DAEMON_RUNNING = ("vitality-client daemon is running; stop it first "
                  "(systemctl --user stop vitality-client)")
"""What a command prints when the daemon must be stopped first but is running."""


def paths_from(env: Mapping[str, str]) -> Paths:
    """The daemon's paths for this environment (`resolve_paths` with the user's home).

    Raises RuntimeError if XDG_RUNTIME_DIR is not set."""
    return resolve_paths(env, Path.home())


def local_time(iso_ts: str, fmt: str = "%H:%M:%S") -> str:
    """An ISO 8601 timestamp (with a UTC offset) shown in this machine's local time, as
    `fmt` (default `HH:MM:SS`)."""
    return datetime.fromisoformat(iso_ts).astimezone().strftime(fmt)


def format_bytes(n: float) -> str:
    """A size in decimal units with one decimal: `1.5 GB`, `3.2 MB`, otherwise kB."""
    if n >= 1e9:
        return f"{n / 1e9:.1f} GB"
    if n >= 1e6:
        return f"{n / 1e6:.1f} MB"
    return f"{n / 1e3:.1f} kB"


def path_lines(paths: StatusPaths) -> list[str]:
    """The three lines `config: ...`, `data: ...` and `state: ...` that `status` and
    `doctor` end with."""
    return [f"config: {paths['config']}", f"data: {paths['data']}", f"state: {paths['state']}"]


def daemon_is_running(socket_path: Path) -> bool:
    """Whether a daemon answers on `socket_path` (asks it for its status)."""
    try:
        asyncio.run(send_command(socket_path, {"cmd": "status"}))
    except DaemonNotRunning:
        return False
    return True
