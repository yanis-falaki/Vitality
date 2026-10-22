"""Talking to the running daemon, and the daemon's saved state.

- `send_command(socket_path, request) -> dict` — what the CLI uses; raises
  `DaemonNotRunning` if nothing answers. Requests: `{"cmd": "status"}`,
  `{"cmd": "pause", "seconds": float | None}`, `{"cmd": "resume"}`.
- `ControlServer(socket_path, handler)` — what the daemon runs; `handler` is an
  async function from request dict to response dict.
- `StateFile(path).load() / .save(State)` — pause state and portal restore token.
- `parse_duration("30m") -> 1800.0`.
"""

from vitality_client.control.channel import ControlServer, DaemonNotRunning, send_command
from vitality_client.control.duration import parse_duration
from vitality_client.control.state import State, StateFile

__all__ = [
    "ControlServer", "DaemonNotRunning", "State", "StateFile", "parse_duration", "send_command",
]
