"""`vitality-server [--config PATH]`: load the settings, open the store, serve on Tailscale."""

import argparse
import asyncio
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

import uvicorn

from vitality_server.app import create_app
from vitality_server.config import load_server_config
from vitality_server.identity import TailscaleIdentity, tailscale_ipv4
from vitality_server.storage import FileStore

DEFAULT_CONFIG = Path("/etc/vitality/server.toml")

log = logging.getLogger(__name__)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vitality-server", description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG,
                        help=f"settings file (default: {DEFAULT_CONFIG})")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    try:
        config = load_server_config(args.config)
        store = FileStore(config.data_dir)
    except (ValueError, OSError) as e:  # bad settings, or a data directory we cannot use
        print(f"vitality-server: {e}", file=sys.stderr)
        return 1

    try:
        recovered = store.recover()
        if recovered:
            log.info("recovery indexed %d interactions the index was missing", recovered)
        if not config.allowed_clients:
            log.warning("allowed_clients is empty: every upload will be refused")
        try:
            ip = asyncio.run(tailscale_ipv4())
        except RuntimeError as e:  # never fall back to listening on every interface
            print(f"vitality-server: cannot find the Tailscale address: {e}", file=sys.stderr)
            return 1
        log.info("serving on %s:%d for %s", ip, config.port, ", ".join(config.allowed_clients) or "nobody")
        uvicorn.run(create_app(store, TailscaleIdentity(), config.allowed_clients),
                    host=ip, port=config.port)
        return 0
    finally:
        store.close()
