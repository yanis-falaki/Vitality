"""Serve: open the store, find the Tailscale address, run the app there.

This is the server itself; it runs as `vitality-server serve` in its container. The rest of
the `vitality-server` command, which loads the settings and inspects the server, is in
`vitality_server.cli`.
"""

import asyncio
import logging
import sys

import uvicorn

from vitality_server.daemon.app import create_app
from vitality_server.shared.config import ServerConfig
from vitality_server.shared.identity import TailscaleIdentity, tailscale_ipv4
from vitality_server.shared.storage import FileStore

log = logging.getLogger(__name__)


def serve(config: ServerConfig) -> int:
    """Run the server until it is stopped; return the process exit code.

    Sets up logging to stderr (INFO), then, in order:

        1. Open the store in `config.data_dir`.
        2. Recover: delete leftover tmp/ folders and index complete interactions the
           index lacks.
        3. Warn if `allowed_clients` is empty (every upload will then be refused).
        4. Find this machine's Tailscale IPv4 address; the server listens only there,
           never on every interface.
        5. Serve until stopped, then close the store.

    Exit codes: 0 after serving and stopping; 1 for an unusable data directory, a failed
    recovery, or no Tailscale address (the reason goes to stderr, nothing is served).
    """
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    try:
        store = FileStore(config.data_dir)
    except OSError as e:  # a data directory we cannot use
        print(f"vitality-server: {e}", file=sys.stderr)
        return 1

    try:
        try:
            recovered = store.recover()
        except OSError as e:
            print(f"vitality-server: {e}", file=sys.stderr)
            return 1
        if recovered:
            log.info("recovery indexed %d interactions the index was missing", recovered)
        if not config.allowed_clients:
            log.warning("SERVER_ALLOWED_CLIENTS is empty: every upload will be refused")
        try:
            ip = asyncio.run(tailscale_ipv4())
        except RuntimeError as e:  # never fall back to listening on every interface
            print(f"vitality-server: cannot find the Tailscale address: {e}", file=sys.stderr)
            return 1
        log.info("serving on %s:%d for %s", ip, config.port, ", ".join(config.allowed_clients) or "nobody")
        uvicorn.run(create_app(store, TailscaleIdentity(), config.allowed_clients),
                    host=ip, port=config.port,
                    proxy_headers=False)  # identity is the socket's address; never a header's
        return 0
    finally:
        store.close()
