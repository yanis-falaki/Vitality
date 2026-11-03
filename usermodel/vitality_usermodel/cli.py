"""The `vitality-usermodel` command: `check` a model folder, or issue the `next-id` of a kind."""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from vitality_usermodel.check import check
from vitality_usermodel.ids import next_id


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command with `argv` (default: the process arguments); returns the exit status.

    `check [--since REV] [--model-dir DIR]` prints `ok` (status 0) or one `path: message` line per
    problem (status 1). `next-id activity|striving [--model-dir DIR]` prints the new ID. Errors
    print `vitality-usermodel: <message>` to stderr, status 1.
    """
    parser = argparse.ArgumentParser(
        prog="vitality-usermodel", description="Work with a Vitality user model folder."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    check_parser = commands.add_parser(
        "check", help="list every problem in the model; exit 1 if there is any"
    )
    check_parser.add_argument(
        "--since", metavar="REV", help="also check what changed since this git revision"
    )
    next_parser = commands.add_parser(
        "next-id", help="issue and print the next activity or striving ID"
    )
    next_parser.add_argument("kind", choices=["activity", "striving"])
    for sub in (check_parser, next_parser):
        sub.add_argument(
            "--model-dir",
            type=Path,
            default=Path("."),
            metavar="DIR",
            help="the model folder (default: .)",
        )
    args = parser.parse_args(argv)
    try:
        if args.command == "check":
            problems = check(args.model_dir, args.since)
            for problem in problems:
                print(f"{problem.path}: {problem.message}")
            if problems:
                return 1
            print("ok")
        else:
            print(next_id(args.model_dir, args.kind))
    except (ValueError, OSError) as e:
        print(f"vitality-usermodel: {e}", file=sys.stderr)
        return 1
    return 0
