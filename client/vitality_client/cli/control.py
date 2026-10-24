"""Commands that talk to the running daemon: pause, resume, toggle, status, capture."""

import asyncio
import json
import sys
from collections.abc import Mapping

from vitality_client.cli.common import NOT_RUNNING, format_bytes, local_time, path_lines, paths_from
from vitality_client.cli.duration import parse_duration
from vitality_client.shared.channel import DaemonNotRunning, send_command


def format_status(status: dict) -> str:
    if status["state"] == "paused":
        until = status.get("paused_until")
        state = "paused" if until is None else f"paused until {local_time(until, '%H:%M')}"
    else:
        state = "capturing"
    last = status["last_capture"]
    lines = [
        f"{state} · {status['captures_today']} captures today · "
        f"last {'never' if last is None else local_time(last)} · "
        f"{format_bytes(status['disk_bytes'])}",
        f"activity: {status['activity']}",
        f"capture: {status['capture']}",
    ]
    if status["disk_warning"]:
        lines.append("warning: disk use is above disk_warning_gb; "
                     "captures are never deleted automatically")
    lines += path_lines(status["paths"])
    return "\n".join(lines)


def command(env: Mapping[str, str], request: dict, as_json: bool = False) -> int:
    try:
        response = asyncio.run(send_command(paths_from(env).socket_path, request))
    except DaemonNotRunning:
        print(NOT_RUNNING, file=sys.stderr)
        return 1
    if not response.get("ok"):
        print(f"vitality-client: {response.get('error', 'request failed')}", file=sys.stderr)
        return 1
    response.pop("ok")
    print(json.dumps(response) if as_json else format_status(response))
    return 0


def pause(env: Mapping[str, str], duration: str | None) -> int:
    request: dict = {"cmd": "pause"}
    if duration is not None:
        try:
            request["seconds"] = parse_duration(duration)
        except ValueError as exc:
            print(exc, file=sys.stderr)
            return 2
    return command(env, request)


def toggle(env: Mapping[str, str]) -> int:
    """Pause if capturing, resume if paused."""
    try:
        status = asyncio.run(send_command(paths_from(env).socket_path, {"cmd": "status"}))
    except DaemonNotRunning:
        print(NOT_RUNNING, file=sys.stderr)
        return 1
    return command(env, {"cmd": "resume" if status.get("state") == "paused" else "pause"})


def capture(env: Mapping[str, str]) -> int:
    """Ask the daemon for one screenshot now and print where it was saved."""
    try:
        response = asyncio.run(send_command(paths_from(env).socket_path, {"cmd": "capture"}))
    except DaemonNotRunning:
        print(NOT_RUNNING, file=sys.stderr)
        return 1
    if not response.get("ok"):
        print(f"vitality-client: {response.get('error', 'capture failed')}", file=sys.stderr)
        return 1
    print(response["path"])
    return 0
