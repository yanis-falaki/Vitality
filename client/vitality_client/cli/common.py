"""Helpers shared by the command modules: paths, messages, formatting."""

import asyncio
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

from vitality_client.shared.config import Paths, resolve_paths
from vitality_client.shared.channel import DaemonNotRunning, send_command

NOT_RUNNING = "vitality-client daemon is not running"
DAEMON_RUNNING = ("vitality-client daemon is running; stop it first "
                  "(systemctl --user stop vitality-client)")


def paths_from(env: Mapping[str, str]) -> Paths:
    return resolve_paths(env, Path.home())


def local_time(iso_ts: str, fmt: str = "%H:%M:%S") -> str:
    return datetime.fromisoformat(iso_ts).astimezone().strftime(fmt)


def format_bytes(n: float) -> str:
    if n >= 1e9:
        return f"{n / 1e9:.1f} GB"
    if n >= 1e6:
        return f"{n / 1e6:.1f} MB"
    return f"{n / 1e3:.1f} kB"


def path_lines(paths: Mapping[str, str]) -> list[str]:
    return [f"config: {paths['config']}", f"data: {paths['data']}", f"state: {paths['state']}"]


def daemon_is_running(socket_path: Path) -> bool:
    try:
        asyncio.run(send_command(socket_path, {"cmd": "status"}))
    except DaemonNotRunning:
        return False
    return True
