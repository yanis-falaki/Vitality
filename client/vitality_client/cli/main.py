"""Argument parsing and dispatch for `vitality-client`."""

import argparse
import os
import sys
from collections.abc import Sequence

from vitality_client.cli import control, data, doctor, run


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vitality-client")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run", help="start the daemon (used by the service)")
    pause = sub.add_parser("pause", help="pause capturing")
    pause.add_argument("duration", nargs="?", help="e.g. 30s, 10m, 2h (default: until resumed)")
    sub.add_parser("resume", help="resume capturing")
    sub.add_parser("toggle", help="pause if capturing, resume if paused")
    status = sub.add_parser("status", help="show daemon status")
    status.add_argument("--json", action="store_true", help="print one line of JSON")
    sub.add_parser("doctor", help="check the setup")
    sub.add_parser("capture-once", help="save one capture now and print its path")
    reset = sub.add_parser("reset", help="delete all captures (asks first; daemon must be stopped)")
    reset.add_argument("--all", action="store_true",
                       help="also delete state (pause state and screen-sharing permission)")
    reset.add_argument("--yes", action="store_true", help="don't ask for confirmation")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    env = os.environ
    try:
        match args.command:
            case "run":
                return run.run(env)
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
            case "capture-once":
                return data.capture_once(env)
            case "reset":
                return data.reset(env, include_state=args.all, assume_yes=args.yes)
    except (RuntimeError, ValueError) as exc:  # no runtime dir, bad config, already running, ...
        print(f"vitality-client: {exc}", file=sys.stderr)
        return 1
    return 2  # unreachable: argparse requires a known command
