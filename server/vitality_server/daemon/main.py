"""Serve: open the store, find the Tailscale address, run the app there, and keep checking
that Tailscale is still reachable.

This is the server itself; it runs as `vitality-server serve` in its container. The rest of
the `vitality-server` command, which loads the settings and inspects the server, is in
`vitality_server.cli`.
"""

import asyncio
import logging
import sys
from collections.abc import Callable, Coroutine
from typing import Any

import uvicorn

from vitality_server.daemon.app import create_app
from vitality_server.shared.config import ServerConfig
from vitality_server.shared.identity import TailscaleIdentity, tailscale_ipv4
from vitality_server.shared.storage import FileStore

log = logging.getLogger(__name__)

IPv4Finder = Callable[[], Coroutine[Any, Any, str]]
"""Returns this machine's Tailscale IPv4 address, or raises RuntimeError (see `tailscale_ipv4`)."""


def serve(config: ServerConfig, *,
          ipv4: IPv4Finder = tailscale_ipv4,
          check_interval_s: float = 30.0,
          failure_limit: int = 3) -> int:
    """Run the server until it is stopped or loses Tailscale; return the process exit code.

    Sets up logging to stderr (INFO), then, in order:

        1. Open the store in `config.data_dir`.
        2. Recover: delete leftover tmp/ folders and the index older servers kept.
        3. Warn if `allowed_clients` is empty (every upload will then be refused).
        4. Find this machine's Tailscale IPv4 address with `ipv4`; the server listens only
           there, never on every interface.
        5. Serve there, and alongside it, every `check_interval_s` seconds, ask `ipv4` again
           (the watchdog). Serving ends when the server is stopped (a signal), or when the
           watchdog gives up: `failure_limit` failed checks in a row (a success resets the
           count), or a different address than the one being served.

    Why the watchdog: the host's tailscaled deletes and recreates /run/tailscale whenever it
    restarts, which leaves the container's mount of it pointing at the deleted folder, so no
    upload can be identified again. Exiting lets Docker (`restart: unless-stopped`) restart
    the container, which sets its mounts up again on the fresh folder. The same goes for a
    changed address, since the server stays bound to the old one. The reason is logged as
    one warning.

    Exit codes: 0 after serving and being stopped; 1 for an unusable data directory, a failed
    recovery, or no Tailscale address (the reason goes to stderr, nothing is served), and 1
    when the watchdog gives up.
    """
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    try:
        store = FileStore(config.data_dir)
    except OSError as e:  # a data directory we cannot use
        print(f"vitality-server: {e}", file=sys.stderr)
        return 1

    try:
        recovered = store.recover()
    except OSError as e:
        print(f"vitality-server: {e}", file=sys.stderr)
        return 1
    if recovered:
        log.info("removed %d unfinished uploads left by a crash", recovered)
    if not config.allowed_clients:
        log.warning("SERVER_ALLOWED_CLIENTS is empty: every upload will be refused")
    try:
        ip = asyncio.run(ipv4())
    except RuntimeError as e:  # never fall back to listening on every interface
        print(f"vitality-server: cannot find the Tailscale address: {e}", file=sys.stderr)
        return 1
    log.info("serving on %s:%d for %s", ip, config.port, ", ".join(config.allowed_clients) or "nobody")
    uvicorn_config = uvicorn.Config(
        create_app(store, TailscaleIdentity(), config.allowed_clients),
        host=ip, port=config.port,
        proxy_headers=False)  # identity is the socket's address; never a header's
    server = uvicorn.Server(uvicorn_config)
    watchdog = _watch_tailscale(ip, ipv4, check_interval_s, failure_limit)
    return asyncio.run(_serve_until_stopped(server, watchdog),
                       loop_factory=uvicorn_config.get_loop_factory())


async def _serve_until_stopped(server: uvicorn.Server, watchdog: Coroutine[Any, Any, str]) -> int:
    """Run `server` and `watchdog` side by side until one of them ends; return the exit code.

    The server ending (it was stopped) gives 0. The watchdog ending gives 1: its result, the
    reason, is logged, and the server is told to stop and awaited (it finishes the requests
    in progress). An exception from the server is raised as before.
    """
    serving = asyncio.create_task(server.serve())
    watching = asyncio.create_task(watchdog)
    await asyncio.wait({serving, watching}, return_when=asyncio.FIRST_COMPLETED)
    if not serving.done():
        log.warning("%s", watching.result())
        server.should_exit = True
        await serving
        return 1
    watching.cancel()
    try:
        await watching
    except asyncio.CancelledError:
        pass
    serving.result()  # raises what the server raised, if anything
    return 0


async def _watch_tailscale(ip: str, ipv4: IPv4Finder, interval_s: float, failure_limit: int) -> str:
    """Every `interval_s` seconds ask `ipv4` for the address; return why serving should end.

    Returns (never raises, never ends otherwise) after `failure_limit` failures in a row, a
    success resetting the count, or as soon as the address is not `ip`.
    """
    failures = 0
    while True:
        await asyncio.sleep(interval_s)
        try:
            current = await ipv4()
        except RuntimeError as e:
            failures += 1
            if failures >= failure_limit:
                return (f"Tailscale unreachable for {failures} checks in a row ({e}); exiting "
                        "so the container is restarted with a fresh /var/run/tailscale")
            continue
        failures = 0
        if current != ip:
            return (f"the Tailscale address changed from {ip} to {current}, but the server is "
                    "bound to the old one; exiting so the container is restarted on the new one")
