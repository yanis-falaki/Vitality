"""Argument parsing and dispatch for `vitality-server`."""

import argparse
from collections.abc import Sequence
from pathlib import Path

from vitality_server.cli.doctor import doctor
from vitality_server.cli.status import status
from vitality_server.config import DEFAULT_CONFIG


def main(argv: Sequence[str] | None = None) -> int:
    """Run one `vitality-server` command; return the process exit code.

    `argv` is the argument list (default: `sys.argv[1:]`). Commands, each handled by its
    module in this package and each taking `--config PATH` (the server's settings file,
    default `/etc/vitality/server.toml`):

        status    what each device has uploaded, disk space, paths
        doctor    check the setup

    Exit codes: 0 on success; 1 when the command fails (unusable settings or data, failed
    checks; the reason is reported on stderr or in the output); 2 for a bad command line,
    which includes giving no command.
    """
    args = _parser().parse_args(argv)
    match args.command:
        case "status":
            return status(args.config)
        case "doctor":
            return doctor(args.config)
    return 2  # unreachable: argparse requires a known command


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vitality-server")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_text in [("status", "show what each device has uploaded, and where things live"),
                            ("doctor", "check the setup")]:
        command = sub.add_parser(name, help=help_text)
        command.add_argument("--config", type=Path, default=DEFAULT_CONFIG,
                             help=f"settings file (default: {DEFAULT_CONFIG})")
    return parser
