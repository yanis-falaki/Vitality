"""The `vitality-modeler` command.

    vitality-modeler replay <dataset> <run>

Replays a dataset's evidence into `<run>/model`, one session per batch, with the settings of
`config.py` from the environment. Exits 0 when all the evidence was committed, 1 when the replay
stopped early (see `<run>/replay.json`) or failed, and 2 on a usage error: bad arguments or
settings, a dataset without `user-context.md`, or a run folder that already has a model. SIGTERM
(as from `docker stop`) stops the replay as Ctrl-C does.
"""

import argparse
import os
import signal
import sys
from collections.abc import Sequence
from pathlib import Path
from types import FrameType

from vitality_modeler.config import load_modeler_config
from vitality_modeler.replay import replay


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vitality-modeler")
    commands = parser.add_subparsers(dest="command", required=True)
    replay_parser = commands.add_parser("replay", help="replay a dataset's evidence into <run>/model")
    replay_parser.add_argument("dataset", type=Path, help="folder with operations/, observations/ and user-context.md")
    replay_parser.add_argument("run", type=Path, help="run folder; gets model/, sessions.jsonl, transcripts/, replay.json")
    try:
        args = parser.parse_args(argv)
    except SystemExit as stop:
        return stop.code if isinstance(stop.code, int) else 2

    previous = signal.signal(signal.SIGTERM, _interrupt)
    try:
        try:
            config = load_modeler_config(os.environ)
            summary = replay(args.dataset, args.run, config)
        except ValueError as error:
            print(f"vitality-modeler: {error}", file=sys.stderr)
            return 2
        except Exception as error:
            print(f"vitality-modeler: {type(error).__name__}: {error}", file=sys.stderr)
            return 1
    finally:
        signal.signal(signal.SIGTERM, previous)
    if summary.stopped is not None:
        print(f"vitality-modeler: stopped: {summary.stopped}", file=sys.stderr)
        return 1
    return 0


def _interrupt(signum: int, frame: FrameType | None) -> None:
    raise KeyboardInterrupt


if __name__ == "__main__":
    sys.exit(main())
