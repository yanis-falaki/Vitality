"""Argument parsing and dispatch for `vitality-server`."""

import argparse
import sys
from collections.abc import Sequence

from vitality_server.cli.healthcheck import healthcheck
from vitality_server.cli.status import status
from vitality_server.shared.config import load_server_config


def main(argv: Sequence[str] | None = None) -> int:
    """Run one `vitality-server` command; return the process exit code.

    `argv` is the argument list (default: `sys.argv[1:]`). Commands, none of which takes
    options (the settings come from environment variables, see `shared/config.py`):

        serve         run the server until it is stopped            daemon/main.py
        status        what each device has uploaded, disk space     status.py
        healthcheck   is the running server answering?              healthcheck.py

    The settings are loaded first and handed to the command.

    Exit codes: 0 on success; 1 when a setting is unusable (`vitality-server: <message>` on
    stderr, naming the variable) or the command fails (the reason is reported on stderr or
    in the output); 2 for a bad command line, which includes giving no command.
    """
    args = _parser().parse_args(argv)
    try:
        config = load_server_config()
    except ValueError as exc:
        print(f"vitality-server: {exc}", file=sys.stderr)
        return 1
    match args.command:
        case "serve":
            # Imported here so status and healthcheck (every 30 s) don't load uvicorn and fastapi.
            from vitality_server.daemon.main import serve
            return serve(config)
        case "status":
            return status(config)
        case "healthcheck":
            return healthcheck(config)
    return 2  # unreachable: argparse requires a known command


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vitality-server")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("serve", help="run the server on the Tailscale address")
    sub.add_parser("status", help="show what each device has uploaded, and the free disk space")
    sub.add_parser("healthcheck", help="exit 0 if the running server answers /health")
    return parser
