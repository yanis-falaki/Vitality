"""Implements `Identity` (see interface.py).

Identity from the local Tailscale daemon, by running the `tailscale` command. The module
also holds what no interface.py covers: `tailscale_ipv4`, which finds the address the
server listens on, and the `Runner` type that both it and `TailscaleIdentity` run commands
through. `TailscaleIdentity` has no public methods beyond `who`.
"""

import asyncio
import ipaddress
import json
from collections.abc import Awaitable, Callable, Sequence

from vitality_server.shared.identity.interface import Identity


Runner = Callable[[Sequence[str]], Awaitable[tuple[int, bytes]]]
"""Runs a command; returns (exit code, stdout). Replaced by a fake in tests."""


async def run_command(argv: Sequence[str]) -> tuple[int, bytes]:
    """Run a command and return (exit code, stdout); stderr is discarded.

    A program that cannot be started (missing, not executable) gives exit code 127, and a
    command that takes longer than 5 seconds is killed and gives 124, so a stuck `tailscale`
    cannot hang a request.
    """
    try:
        process = await asyncio.create_subprocess_exec(
            *argv, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL)
    except OSError:  # no such program, or not executable
        return _NOT_FOUND, b""
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), _TIMEOUT_S)
    except TimeoutError:
        process.kill()
        await process.wait()
        return _TIMED_OUT, b""
    return process.returncode or 0, stdout


async def tailscale_ipv4(run: Runner = run_command) -> str:
    """This machine's Tailscale IPv4 address (`tailscale ip -4`, first line).

    Raises RuntimeError if Tailscale is missing, not running, or does not give an IPv4
    address in Tailscale's range, 100.64.0.0/10: the server binds exactly what this
    returns, so anything else (0.0.0.0 above all) would expose it beyond the tailnet.
    """
    code, stdout = await run(["tailscale", "ip", "-4"])
    lines = stdout.decode(errors="replace").splitlines() if code == 0 else []
    if not lines:
        raise RuntimeError(f"`tailscale ip -4` gave no address (exit code {code}); "
                           "is Tailscale installed and running?")
    text = lines[0].strip()
    try:
        address = ipaddress.IPv4Address(text)
    except ValueError:
        raise RuntimeError(f"`tailscale ip -4` gave {text!r}, not an IPv4 address") from None
    if address not in _TAILSCALE_RANGE:
        raise RuntimeError(f"`tailscale ip -4` gave {text}, which is outside Tailscale's "
                           f"range {_TAILSCALE_RANGE}; refusing to listen on it")
    return str(address)


class TailscaleIdentity(Identity):
    """Asks `tailscale whois` which device owns an address, and accepts only devices on this
    server's own tailnet.

    The device name is the first label of the node's MagicDNS name (`Node.Name`, e.g.
    "laptop.tail1234.ts.net." -> "laptop"), and that name must end in this server's tailnet
    suffix (its own `Self.DNSName` from `tailscale status --json` minus the first label).
    That rules out nodes shared in from another tailnet, which may carry any first label.
    `ComputedName` is never used: it can follow the device's OS hostname. The suffix is
    read once, on the first request, and kept; a failed read is retried on the next one.
    """

    def __init__(self, run: Runner = run_command) -> None:
        self._run = run
        self._suffix: str | None = None  # e.g. "tail1234.ts.net", once known

    async def who(self, ip: str) -> str | None:
        code, stdout = await self._run(["tailscale", "whois", "--json", ip])
        if code != 0:
            return None
        node = _field(stdout, "Node")
        name = node.get("Name") if isinstance(node, dict) else None
        label, rest = _split_dns_name(name)
        suffix = await self._tailnet_suffix()
        if not label or suffix is None or rest != suffix:
            return None
        return label

    async def _tailnet_suffix(self) -> str | None:
        if self._suffix is None:
            code, stdout = await self._run(["tailscale", "status", "--json"])
            me = _field(stdout, "Self") if code == 0 else None
            _, rest = _split_dns_name(me.get("DNSName") if isinstance(me, dict) else None)
            self._suffix = rest or None
        return self._suffix


_TIMEOUT_S = 5.0
_NOT_FOUND = 127  # the shell's exit code for "command not found"
_TIMED_OUT = 124  # the exit code of timeout(1)
_TAILSCALE_RANGE = ipaddress.IPv4Network("100.64.0.0/10")  # the CGNAT block Tailscale assigns


def _field(stdout: bytes, key: str) -> object:
    """`key` of the JSON object in `stdout`, or None if it is not a JSON object."""
    try:
        data = json.loads(stdout)
    except ValueError:
        return None
    return data.get(key) if isinstance(data, dict) else None


def _split_dns_name(name: object) -> tuple[str, str]:
    """("laptop", "tail1234.ts.net") for "laptop.tail1234.ts.net."; ("", "") if not a name.

    The tailnet part is lower-cased and loses its trailing dot (DNS names ignore case)."""
    if not isinstance(name, str):
        return "", ""
    label, _, rest = name.rstrip(".").partition(".")
    return label, rest.lower()
