"""The `vitality-modeler` command.

    vitality-modeler run [--model /model] [--state /state] [--captures /captures]
    vitality-modeler status [--model /model] [--state /state]
    vitality-modeler replay <model> <state> [--captures <folder>]

`run` is the service: the scheduler (`scheduler.py`), until stopped (SIGTERM or Ctrl-C: exit 0) or
paused after repeated failures (exit 1). `status` prints one line (`status.py`). `replay` is the lab's:
it runs sessions on the model folder `<model>` (made by `vitality_modeler.replay.prepare`, on the host)
until all its evidence is committed, writing its records to `<state>`, with the settings of `config.py`
from the environment. In the lab's container these are `/model` and `/state`. Exits 0 when all the
evidence was committed, 1 when the replay stopped early (see `<state>/replay.json`) or failed, and 2
on a usage error: bad arguments or settings, or a folder that is not a prepared model; an
unexpected error is printed with its traceback. SIGTERM
(as from `docker stop`) stops the replay as Ctrl-C does.
"""

import argparse
import os
import time
from datetime import datetime
import signal
import sys
import traceback
from collections.abc import Callable, Sequence
from pathlib import Path
from types import FrameType

from vitality_modeler.config import load_modeler_config
from vitality_modeler.replay import replay
from vitality_modeler.scheduler import Scheduler
from vitality_modeler.status import status_line


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vitality-modeler")
    commands = parser.add_subparsers(dest="command", required=True)
    replay_parser = commands.add_parser("replay", help="run sessions on <model> until nothing waits")
    replay_parser.add_argument("model", type=Path, help="the prepared model folder (a git repo with evidence waiting)")
    replay_parser.add_argument("state", type=Path, help="gets sessions.jsonl, transcripts/, replay.json")
    replay_parser.add_argument("--captures", metavar="FOLDER",
                               help="the screenshots folder the sessions may read (in the container: /captures)")
    run_parser = commands.add_parser("run", help="the service: schedule sessions on the model until stopped")
    status_parser = commands.add_parser("status", help="print one line saying what the service is doing")
    for sub in (run_parser, status_parser):
        sub.add_argument("--model", type=Path, default=Path("/model"), help="the model folder (default /model)")
        sub.add_argument("--state", type=Path, default=Path("/state"), help="the state folder (default /state)")
    run_parser.add_argument("--captures", default="/captures", help="the screenshots folder (default /captures)")
    try:
        args = parser.parse_args(argv)
    except SystemExit as stop:
        return stop.code if isinstance(stop.code, int) else 2
    if args.command == "status":
        return _status(args.model, args.state)
    if args.command == "run":
        return _run(args.model, args.state, args.captures)

    previous = signal.signal(signal.SIGTERM, _interrupt)
    try:
        try:
            config = load_modeler_config(os.environ)
            summary = replay(args.model, args.state, config, captures=args.captures)
        except ValueError as error:
            print(f"vitality-modeler: {error}", file=sys.stderr)
            return 2
        except Exception as error:
            traceback.print_exc()
            print(f"vitality-modeler: {type(error).__name__}: {error}", file=sys.stderr)
            return 1
    finally:
        signal.signal(signal.SIGTERM, previous)
    if summary.stopped is not None:
        print(f"vitality-modeler: stopped: {summary.stopped}", file=sys.stderr)
        return 1
    return 0


def _status(model: Path, state: Path) -> int:
    try:
        print(status_line(model, state, datetime.now().astimezone()))
    except (OSError, RuntimeError) as error:
        print(f"vitality-modeler: cannot read {model}: {error}", file=sys.stderr)
        return 1
    return 0


def _run(model: Path, state: Path, captures: str, sleep: Callable[[float], object] = time.sleep) -> int:
    # When paused, stay up doing nothing until stopped: Compose restarts a service that exits, which
    # would run three more failing sessions, pause, exit, and so on.
    previous = signal.signal(signal.SIGTERM, _interrupt)
    try:
        config = load_modeler_config(os.environ)
        if Scheduler(model, state, config, captures=captures).run() == 0:
            return 0
        try:
            while True:
                sleep(3600)
        except KeyboardInterrupt:
            return 1
    except (ValueError, RuntimeError) as error:
        print(f"vitality-modeler: {error}", file=sys.stderr)
        return 2
    finally:
        signal.signal(signal.SIGTERM, previous)


def _interrupt(signum: int, frame: FrameType | None) -> None:
    raise KeyboardInterrupt


if __name__ == "__main__":
    sys.exit(main())
