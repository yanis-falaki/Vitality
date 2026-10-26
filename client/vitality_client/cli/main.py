"""Argument parsing and dispatch for `vitality-client`."""

import argparse
import os
import sys
from collections.abc import Sequence

from vitality_client.cli import control, doctor, reset, upgrade


def main(argv: Sequence[str] | None = None) -> int:
    """Run one `vitality-client` command; return the process exit code.

    `argv` is the argument list (default: `sys.argv[1:]`). Commands, each handled by its
    module in this package:

        pause [DURATION]    pause capturing (e.g. 30s, 10m, 2h; default: until resumed)
        resume              resume capturing
        toggle              pause if capturing, resume if paused
        status [--json]     show the daemon's status
        capture             take a screenshot now and print its path
        doctor              check the setup
        reset [--all] [--yes]   delete all captures (the daemon must be stopped)
        upgrade             reinstall the client from this repo's main, then run doctor

    Exit codes: 0 on success; 1 when the command fails (daemon not running or replying
    with an error, failed checks, reset or upgrade refused or declined, or a RuntimeError/ValueError
    such as a missing XDG_RUNTIME_DIR or a bad config, reported on stderr); 2 for a bad
    command line or pause duration.
    """
    args = _parser().parse_args(argv)
    env = os.environ
    try:
        match args.command:
            case "pause":
                return control.pause(env, args.duration)
            case "resume":
                return control.command(env, {"cmd": "resume"})
            case "toggle":
                return control.toggle(env)
            case "status":
                return control.command(env, {"cmd": "status"}, as_json=args.json)
            case "doctor":
                return doctor.doctor(env)
            case "capture":
                return control.capture(env)
            case "reset":
                return reset.reset(env, include_state=args.all, assume_yes=args.yes)
            case "upgrade":
                return upgrade.upgrade(env)
    except (RuntimeError, ValueError) as exc:  # no runtime dir, bad config, already running, ...
        print(f"vitality-client: {exc}", file=sys.stderr)
        return 1
    return 2  # unreachable: argparse requires a known command


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vitality-client")
    sub = parser.add_subparsers(dest="command", required=True)
    pause = sub.add_parser("pause", help="pause capturing")
    pause.add_argument("duration", nargs="?", help="e.g. 30s, 10m, 2h (default: until resumed)")
    sub.add_parser("resume", help="resume capturing")
    sub.add_parser("toggle", help="pause if capturing, resume if paused")
    status = sub.add_parser("status", help="show daemon status")
    status.add_argument("--json", action="store_true", help="print one line of JSON")
    sub.add_parser("doctor", help="check the setup")
    sub.add_parser("capture", help="take a screenshot now (asks the daemon) and print its path")
    reset = sub.add_parser("reset", help="delete all captures (asks first; daemon must be stopped)")
    reset.add_argument("--all", action="store_true",
                       help="also delete state (pause state and screen-sharing permission)")
    reset.add_argument("--yes", action="store_true", help="don't ask for confirmation")
    sub.add_parser("upgrade", help="reinstall the client from this repo's main, then run doctor")
    return parser
