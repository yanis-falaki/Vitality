"""Talking to the running daemon, and the daemon's saved state.

Contract for saved state: `StateStore` and `State` (interface.py); implementation
`StateFile` (state.py).

The CLI ↔ daemon channel (channel.py):
- `send_command(socket_path, request) -> dict` — what the CLI uses; raises
  `DaemonNotRunning` if nothing answers. Requests: `{"cmd": "status"}`,
  `{"cmd": "pause", "seconds": float | None}`, `{"cmd": "resume"}` (each replies with the
  daemon's status) and `{"cmd": "capture"}` (replies `{"ok": True, "path": ...}`).
  The daemon's side of these is `Daemon.handle` in daemon/daemon.py.
- `ControlServer(socket_path, handler)` — what the daemon runs; `handler` is an
  async function from request dict to response dict.

"""

from vitality_client.control.channel import ControlServer, DaemonNotRunning, send_command
from vitality_client.control.interface import State, StateStore
from vitality_client.control.state import StateFile

__all__ = [
    "ControlServer", "DaemonNotRunning", "State", "StateFile", "StateStore",
    "send_command",
]
