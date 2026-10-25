"""Commands that talk to the running daemon: pause, resume, toggle, status, capture."""

import asyncio
import json
import sys
from collections.abc import Mapping

from vitality_client.cli.common import NOT_RUNNING, format_bytes, local_time, path_lines, paths_from
from vitality_client.cli.duration import parse_duration
from vitality_client.shared.channel import (
    DaemonNotRunning,
    PauseRequest,
    Reply,
    Request,
    ResumeRequest,
    Status,
    StatusRequest,
    UploadStatus,
    send_command,
)


def _upload_line(upload: UploadStatus) -> str:
    if upload["state"] == "disabled":
        return "upload: disabled"
    last = upload["last_upload"]
    return (f"upload: {upload['state']} · {upload['pending']} pending · "
            f"last {'never' if last is None else local_time(last)}")


def format_status(status: Status) -> str:
    if status["state"] == "paused":
        until = status["paused_until"]
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
        _upload_line(status["upload"]),
    ]
    if status["disk_warning"]:
        lines.append("warning: disk use is above disk_warning_gb; "
                     "captures are never deleted automatically")
    lines += path_lines(status["paths"])
    return "\n".join(lines)


def ask(env: Mapping[str, str], request: Request) -> Reply | None:
    """Send one request to the daemon. If it isn't running, say so and return None."""
    try:
        return asyncio.run(send_command(paths_from(env).socket_path, request))
    except DaemonNotRunning:
        print(NOT_RUNNING, file=sys.stderr)
        return None


def _status(reply: Reply | None) -> Status | None:
    """The `Status` in a reply; prints the daemon's error (and returns None) otherwise."""
    if reply is None:
        return None
    if reply["ok"] is False:
        print(f"vitality-client: {reply['error']}", file=sys.stderr)
        return None
    if "state" not in reply:
        print("vitality-client: unexpected reply from the daemon", file=sys.stderr)
        return None
    return reply


def command(env: Mapping[str, str], request: StatusRequest | PauseRequest | ResumeRequest,
            as_json: bool = False) -> int:
    """Send a status/pause/resume request and print the daemon's status."""
    status = _status(ask(env, request))
    if status is None:
        return 1
    print(json.dumps({k: v for k, v in status.items() if k != "ok"}) if as_json
          else format_status(status))
    return 0


def pause(env: Mapping[str, str], duration: str | None) -> int:
    request: PauseRequest = {"cmd": "pause"}
    if duration is not None:
        try:
            request["seconds"] = parse_duration(duration)
        except ValueError as exc:
            print(exc, file=sys.stderr)
            return 2
    return command(env, request)


def toggle(env: Mapping[str, str]) -> int:
    """Pause if capturing, resume if paused."""
    status = _status(ask(env, {"cmd": "status"}))
    if status is None:
        return 1
    if status["state"] == "paused":
        return command(env, {"cmd": "resume"})
    return command(env, {"cmd": "pause"})


def capture(env: Mapping[str, str]) -> int:
    """Ask the daemon for one screenshot now and print where it was saved."""
    reply = ask(env, {"cmd": "capture"})
    if reply is None:
        return 1
    if reply["ok"] is False:
        print(f"vitality-client: {reply['error']}", file=sys.stderr)
        return 1
    if "path" not in reply:
        print("vitality-client: unexpected reply from the daemon", file=sys.stderr)
        return 1
    print(reply["path"])
    return 0
