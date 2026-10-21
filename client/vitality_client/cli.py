"""The `vitality-client` command line."""

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path

import ulid

from vitality_client.config import Paths, load_config, resolve_paths
from vitality_client.control import (
    DaemonNotRunning, State, StateFile, parse_duration, send_command,
)

FIRST_FRAME_TIMEOUT = 10.0
NOT_RUNNING = "vitality-client daemon is not running"


def _local_time(iso_ts: str, fmt: str = "%H:%M:%S") -> str:
    return datetime.fromisoformat(iso_ts).astimezone().strftime(fmt)


def _format_bytes(n: float) -> str:
    if n >= 1e9:
        return f"{n / 1e9:.1f} GB"
    if n >= 1e6:
        return f"{n / 1e6:.1f} MB"
    return f"{n / 1e3:.1f} kB"


def format_status(status: dict) -> str:
    if status["state"] == "paused":
        until = status.get("paused_until")
        state = "paused" if until is None else f"paused until {_local_time(until, '%H:%M')}"
    else:
        state = "capturing"
    last = status["last_capture"]
    lines = [
        f"{state} · {status['captures_today']} captures today · "
        f"last {'never' if last is None else _local_time(last)} · "
        f"{_format_bytes(status['disk_bytes'])}",
        f"activity: {status['activity']}",
        f"capture: {status['capture']}",
    ]
    if status["disk_warning"]:
        lines.append("warning: disk use is above disk_warning_gb; "
                     "captures are never deleted automatically")
    lines += _path_lines(status["paths"])
    return "\n".join(lines)


def _path_lines(paths: Mapping[str, str]) -> list[str]:
    return [f"config: {paths['config']}", f"data: {paths['data']}", f"state: {paths['state']}"]


def _paths(env: Mapping[str, str]) -> Paths:
    return resolve_paths(env, Path.home())


def _require_wayland(env: Mapping[str, str]) -> bool:
    session = env.get("XDG_SESSION_TYPE", "")
    if session != "wayland":
        print(f"vitality-client requires a Wayland session (XDG_SESSION_TYPE={session})",
              file=sys.stderr)
        return False
    return True


# -- commands ------------------------------------------------------------------------------

def _run(env: Mapping[str, str]) -> int:
    if not _require_wayland(env):
        return 1
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(levelname)s %(name)s: %(message)s")
    # Imported here: these pull in GStreamer and evdev, which status/pause/doctor don't need.
    from vitality_client.activity import ActivityWatcher
    from vitality_client.daemon import Daemon
    from vitality_client.frames.portal import PortalFrameSource

    paths = _paths(env)
    config = load_config(paths.config_file)
    token = StateFile(paths.state_file).load().restore_token

    async def main() -> None:
        # The daemon owns the state file; the frame source reports new tokens through it.
        holder: list[Daemon] = []
        frames = PortalFrameSource(token, lambda t: holder[0].save_restore_token(t))
        daemon = Daemon(config, paths, frames, ActivityWatcher)
        holder.append(daemon)
        await daemon.run()

    asyncio.run(main())
    return 0


def _command(env: Mapping[str, str], request: dict, as_json: bool = False) -> int:
    try:
        response = asyncio.run(send_command(_paths(env).socket_path, request))
    except DaemonNotRunning:
        print(NOT_RUNNING, file=sys.stderr)
        return 1
    if not response.get("ok"):
        print(f"vitality-client: {response.get('error', 'request failed')}", file=sys.stderr)
        return 1
    response.pop("ok")
    print(json.dumps(response) if as_json else format_status(response))
    return 0


def _pause(env: Mapping[str, str], duration: str | None) -> int:
    request: dict = {"cmd": "pause"}
    if duration is not None:
        try:
            request["seconds"] = parse_duration(duration)
        except ValueError as exc:
            print(exc, file=sys.stderr)
            return 2
    return _command(env, request)


def _doctor(env: Mapping[str, str]) -> int:
    from vitality_client.doctor import run_checks

    paths = _paths(env)
    checks = run_checks(env, paths)
    for c in checks:
        print(f"{'✓' if c.ok else '✗'} {c.name}" + ("" if c.ok else f" — {c.hint}"))
    print("\n".join(_path_lines({
        "config": str(paths.config_file), "data": str(paths.data_dir),
        "state": str(paths.state_file),
    })))
    return 0 if all(c.ok for c in checks) else 1


def _capture_once(env: Mapping[str, str]) -> int:
    if not _require_wayland(env):
        return 1
    from vitality_client.frames.portal import PortalFrameSource
    from vitality_client.policy import SnapshotRequest
    from vitality_client.store import Store
    from vitality_client.writer import Writer

    paths = _paths(env)
    config = load_config(paths.config_file)
    state_file = StateFile(paths.state_file)

    def save_token(token: str) -> None:
        state = state_file.load()
        state_file.save(State(state.paused, state.paused_until, token))

    async def main() -> Path | None:
        frames = PortalFrameSource(state_file.load().restore_token, save_token)
        try:
            await frames.start()  # may wait on the permission dialog
            deadline = time.monotonic() + FIRST_FRAME_TIMEOUT
            while (frame := frames.latest_frame()) is None:
                if time.monotonic() >= deadline:
                    return None
                await asyncio.sleep(0.1)
        finally:
            await frames.close()
        store = Store(paths.data_dir / "index.sqlite")
        try:
            writer = Writer(paths.data_dir, store, config.webp_quality, lambda: str(ulid.ULID()))
            row = writer.write(frame, SnapshotRequest("after", str(ulid.ULID()), time.time()))
        finally:
            store.close()
        return (paths.data_dir / row.path).resolve()

    path = asyncio.run(main())
    if path is None:
        print("no frame received from the screen capture portal", file=sys.stderr)
        return 1
    print(path)
    return 0


# -- entry point ---------------------------------------------------------------------------

def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vitality-client")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run", help="start the daemon (used by the service)")
    pause = sub.add_parser("pause", help="pause capturing")
    pause.add_argument("duration", nargs="?", help="e.g. 30s, 10m, 2h (default: until resumed)")
    sub.add_parser("resume", help="resume capturing")
    status = sub.add_parser("status", help="show daemon status")
    status.add_argument("--json", action="store_true", help="print one line of JSON")
    sub.add_parser("doctor", help="check the setup")
    sub.add_parser("capture-once", help="save one capture now and print its path")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    env = os.environ
    try:
        match args.command:
            case "run":
                return _run(env)
            case "pause":
                return _pause(env, args.duration)
            case "resume":
                return _command(env, {"cmd": "resume"})
            case "status":
                return _command(env, {"cmd": "status"}, as_json=args.json)
            case "doctor":
                return _doctor(env)
            case "capture-once":
                return _capture_once(env)
    except (RuntimeError, ValueError) as exc:  # no runtime dir, bad config, already running, ...
        print(f"vitality-client: {exc}", file=sys.stderr)
        return 1
    return 2  # unreachable: argparse requires a known command
