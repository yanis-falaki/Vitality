"""Identity from the local Tailscale daemon, by running the `tailscale` command."""

import asyncio
import json
from collections.abc import Awaitable, Callable, Sequence

from vitality_server.identity.interface import Identity

Runner = Callable[[Sequence[str]], Awaitable[tuple[int, bytes]]]
"""Runs a command; returns (exit code, stdout). Replaced by a fake in tests."""

_TIMEOUT_S = 5.0
_NOT_FOUND = 127  # the shell's exit code for "command not found"
_TIMED_OUT = 124  # the exit code of timeout(1)


async def run_command(argv: Sequence[str]) -> tuple[int, bytes]:
    """Run a command and return (exit code, stdout); stderr is discarded.

    A missing program gives exit code 127, and a command that takes longer than
    5 seconds is killed and gives 124, so a stuck `tailscale` cannot hang a request.
    """
    try:
        process = await asyncio.create_subprocess_exec(
            *argv, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL)
    except FileNotFoundError:
        return _NOT_FOUND, b""
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), _TIMEOUT_S)
    except TimeoutError:
        process.kill()
        await process.wait()
        return _TIMED_OUT, b""
    return process.returncode or 0, stdout


class TailscaleIdentity(Identity):
    """Asks `tailscale whois` which device owns an address on the tailnet."""

    def __init__(self, run: Runner = run_command) -> None:
        self._run = run

    async def who(self, ip: str) -> str | None:
        code, stdout = await self._run(["tailscale", "whois", "--json", ip])
        if code != 0:
            return None
        try:
            node = json.loads(stdout).get("Node")
        except (ValueError, AttributeError):  # not JSON, or JSON that is not an object
            return None
        if not isinstance(node, dict):
            return None
        computed = node.get("ComputedName")
        if isinstance(computed, str) and computed:
            return computed
        name = node.get("Name")  # "laptop.tail1.ts.net." -> "laptop"
        if isinstance(name, str):
            return name.split(".")[0] or None
        return None


async def tailscale_ipv4(run: Runner = run_command) -> str:
    """This machine's Tailscale IPv4 address (`tailscale ip -4`, first line).

    Raises RuntimeError if Tailscale is missing, not running, or gives no address.
    """
    code, stdout = await run(["tailscale", "ip", "-4"])
    lines = stdout.decode(errors="replace").split() if code == 0 else []
    if not lines:
        raise RuntimeError(f"`tailscale ip -4` gave no address (exit code {code}); "
                           "is Tailscale installed and running?")
    return lines[0]
