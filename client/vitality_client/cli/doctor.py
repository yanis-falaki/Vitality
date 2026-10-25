"""`vitality-client doctor`: check the setup and show whether capture is paused."""

import asyncio
from collections.abc import Mapping
from datetime import datetime

from vitality_client.cli.common import local_time, path_lines, paths_from
from vitality_client.shared.channel import DaemonNotRunning, Status, send_command
from vitality_client.shared.state import State, StateFile


# == Contract =============================================================================

def capture_state_line(status: Status | None, saved: State) -> str:
    """One line saying whether capture is paused: live from the daemon (`status`) if it
    runs, else from the saved state; local times, `HH:MM`."""
    if status is not None:
        if status["state"] != "paused":
            return "daemon: running, capturing"
        until = status["paused_until"]
        return "daemon: running, " + _pause_text(True, local_time(until, "%H:%M") if until else None)
    until = saved.paused_until
    when = datetime.fromtimestamp(until).strftime("%H:%M") if until is not None else None
    return "daemon: not running; saved state: " + _pause_text(saved.paused, when)


def doctor(env: Mapping[str, str]) -> int:
    """Print one line per setup check (`✓ name`, or `✗ name — what to do`), then whether
    capture is paused and where the config, data and state live.

    Works with the daemon stopped (it then reads the saved pause state). Returns 0 if every
    check passed, else 1. Raises RuntimeError if XDG_RUNTIME_DIR is not set.
    """
    from vitality_client.cli.checks import run_checks

    paths = paths_from(env)
    checks = run_checks(env, paths)
    for c in checks:
        print(f"{'✓' if c.ok else '✗'} {c.name}" + ("" if c.ok else f" — {c.hint}"))
    try:
        reply = asyncio.run(send_command(paths.socket_path, {"cmd": "status"}))
    except DaemonNotRunning:
        reply = None
    status = reply if reply is not None and reply["ok"] and "state" in reply else None
    print(capture_state_line(status, StateFile(paths.state_file).load()))
    print("\n".join(path_lines({
        "config": str(paths.config_file), "data": str(paths.data_dir),
        "state": str(paths.state_file),
    })))
    return 0 if all(c.ok for c in checks) else 1


# == Implementation =======================================================================

def _pause_text(paused: bool, until: str | None) -> str:
    if not paused:
        return "not paused"
    return f"paused until {until}" if until else "paused"
