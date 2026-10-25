"""`vitality-server status`: what each device has uploaded, disk space, and where things live."""

import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from vitality_server.cli.display import format_bytes, format_received
from vitality_server.cli.paths import path_lines
from vitality_server.shared.config import load_server_config
from vitality_server.shared.storage import DeviceSummary, read_summary


GROUP_HINT = "are you in the vitality group? log in again after install.sh adds you"


def status(config_path: Path) -> int:
    """Print one line per device in the index (sorted by name), the free disk space and the
    paths block; return the exit code.

        laptop: 595 interactions · 1835 shots · 201.0 MB · last received 21:30:37
        disk: 19.7 GB free
        config:   /etc/vitality/server.toml
        ...

    Sizes come from the index, not from walking the folders. `last received` is shown in
    local time, as `HH:MM:SS` if today, else `YYYY-MM-DD HH:MM`. With nothing stored it prints
    `no interactions stored yet`. Changes nothing on disk.

    Returns 0, or 1 if the settings cannot be loaded (`vitality-server: <reason>` on stderr,
    nothing on stdout) or the data folder or index cannot be read (`cannot read <path>:
    <reason>` on stderr; the free space and the paths block are still printed where known).
    """
    try:
        config = load_server_config(config_path)
    except (ValueError, OSError) as exc:
        print(f"vitality-server: {exc}", file=sys.stderr)
        return 1

    index = config.data_dir / "index.sqlite"
    ok = True
    try:
        devices = read_summary(config.data_dir)
    except (OSError, sqlite3.Error) as exc:
        print(f"cannot read {index}: {_reason(exc)}", file=sys.stderr)
        ok = False
    else:
        now = _now()
        if not devices:
            print("no interactions stored yet")
        for device in devices:
            print(_device_line(device, now))
    try:
        print(f"disk: {format_bytes(shutil.disk_usage(config.data_dir).free)} free")
    except OSError as exc:
        if ok:  # the index was read, so say what is wrong with the folder
            print(f"cannot read {config.data_dir}: {_reason(exc)}", file=sys.stderr)
            ok = False
    print("\n".join(path_lines(config_path, config.data_dir)))
    return 0 if ok else 1


def _now() -> datetime:
    return datetime.now().astimezone()


def _device_line(device: DeviceSummary, now: datetime) -> str:
    received = format_received(device.last_received, now) if device.last_received else "never"
    return (f"{device.client}: {device.interactions} interactions · {device.shots} shots · "
            f"{format_bytes(device.bytes)} · last received {received}")


def _reason(exc: Exception) -> str:
    """Why a read failed, short: `Permission denied`, `No such file or directory`, ...;
    a permission problem also says what to check: the data folder is readable by the
    `vitality` group, which `install.sh` adds the installing user to."""
    text = (exc.strerror or str(exc)) if isinstance(exc, OSError) else str(exc)
    return text + (f"; {GROUP_HINT}" if isinstance(exc, PermissionError) else "")
