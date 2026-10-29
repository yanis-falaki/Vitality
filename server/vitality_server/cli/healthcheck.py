"""`vitality-server healthcheck`: is the running server answering? Compose runs it every 30 s."""

import asyncio
import sys
import urllib.error
import urllib.request
from collections.abc import Awaitable, Callable

from vitality_protocol import HEALTH_PATH

from vitality_server.shared.config import ServerConfig
from vitality_server.shared.identity import tailscale_ipv4


def healthcheck(config: ServerConfig,
                ipv4: Callable[[], Awaitable[str]] = tailscale_ipv4,
                fetch: Callable[[str], int] = lambda url: _get_status(url)) -> int:  # defined below
    """Ask the server on this machine whether it is up; return the exit code.

    The server listens only on the Tailscale address, so this finds that address with `ipv4`
    and fetches `http://<address>:<port>/health` with `fetch` (URL in, HTTP status code out;
    both are parameters so tests need no network). Prints nothing when healthy.

    Returns 0 if the answer is 200 within 5 seconds. Returns 1, with one line on stderr
    saying why (`vitality-server: unhealthy: <reason>`), if there is no Tailscale address,
    the server cannot be reached or is too slow, or it answers with another status.
    """
    try:
        address = asyncio.run(ipv4())
    except RuntimeError as exc:
        return _unhealthy(f"cannot find the Tailscale address: {exc}")
    url = f"http://{address}:{config.port}{HEALTH_PATH}"
    try:
        code = fetch(url)
    except OSError as exc:
        return _unhealthy(f"{url}: {exc}")
    if code != 200:
        return _unhealthy(f"{url} answered {code}")
    return 0


def _get_status(url: str) -> int:
    """The HTTP status code of `GET url`, waiting at most 5 seconds. An error status such as
    503 is returned, not raised; an unreachable server raises OSError."""
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code


def _unhealthy(reason: str) -> int:
    print(f"vitality-server: unhealthy: {reason}", file=sys.stderr)
    return 1
