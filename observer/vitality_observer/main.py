"""`vitality-observer run | status`: the observer's one command.

`run` is the observer itself, as the `observer` container runs it. It runs the loop
(`vitality_observer.loop`) and a watcher on the captures that wakes the loop when something
new arrives. `status` (`status.py`) prints how far the observer has got.
"""

import argparse
import asyncio
import logging
import os
import signal
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from watchfiles import Change, awatch

from vitality_observer.config import ObserverConfig, load_observer_config
from vitality_observer.loop import ObserveLoop
from vitality_observer.provider.anthropic_api import AnthropicObserver
from vitality_observer.provider.interface import Observer
from vitality_observer.status import status

log = logging.getLogger("vitality_observer")


def main(argv: Sequence[str] | None = None) -> int:
    """Run one `vitality-observer` command; return the process exit code.

    `argv` is the argument list (default: `sys.argv[1:]`). Commands, none of which takes
    options (the settings come from environment variables, see `config.py`):

        run      keep every interaction observed, until stopped     run, below
        status   how many are observed, and how the last call went  status.py

    The settings are loaded first and handed to the command.

    Exit codes: 0 on success; 1 when a setting is unusable (`vitality-observer: <message>` on
    stderr, naming the variable) or the command fails (the reason is on stderr); 2 for a bad
    command line, which includes giving no command.
    """
    args = _parser().parse_args(argv)
    try:
        config = load_observer_config()
    except ValueError as exc:
        print(f"vitality-observer: {exc}", file=sys.stderr)
        return 1
    match args.command:
        case "run":
            return run(config)
        case "status":
            return status(config)
    return 2  # unreachable: argparse requires a known command


def run(config: ObserverConfig) -> int:
    """Run the observer until it is stopped; return the process exit code. In order:

        1. Set up logging to stderr (the container's log adds the time).
        2. Refuse to run as root: the observer must run as the owner's user (`VITALITY_UID`),
           so that the captures stay read-only to it.
        3. Refuse to run without an API key: `ANTHROPIC_API_KEY` unset, empty or only spaces.
        4. Create `state_dir` if it is missing.
        5. Run `serve` with the Anthropic API (`AnthropicObserver`) until SIGTERM or Ctrl-C.

    Exit codes: 0 after running and stopping; 1 for running as root, a missing API key or a
    `state_dir` that cannot be created (the reason goes to stderr).
    """
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(levelname)s %(name)s: %(message)s")
    if os.geteuid() == 0:
        print("vitality-observer: refusing to run as root; set VITALITY_UID to your own user",
              file=sys.stderr)
        return 1
    if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        print("vitality-observer: ANTHROPIC_API_KEY is not set", file=sys.stderr)
        return 1
    try:
        config.state_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(f"vitality-observer: {e}", file=sys.stderr)
        return 1

    try:
        asyncio.run(_serve_until_stopped(config, AnthropicObserver(config)))
    except KeyboardInterrupt:
        pass
    return 0


async def serve(config: ObserverConfig, observer: Observer, rescan_seconds: float = 60) -> None:
    """Observe until cancelled: the loop and the captures watcher, together.

    Returns only by cancellation (`CancelledError`), which stops both. The watcher never ends
    the loop: if it cannot run, the loop's rescan every `rescan_seconds` still finds new
    interactions, only later. An unexpected error in the loop itself propagates.
    """
    loop = ObserveLoop(config, observer, rescan_seconds=rescan_seconds)
    log.info("observing %s into %s", config.captures_dir, config.model_dir / "observations")
    async with asyncio.TaskGroup() as tasks:
        tasks.create_task(loop.run())
        tasks.create_task(watch_captures(config.captures_dir, loop.wake))


async def watch_captures(captures_dir: Path, wake: Callable[[], None]) -> None:
    """Call `wake` whenever a file or directory is added anywhere under `captures_dir`.

    Never raises (except `CancelledError`): a missing directory, or a watcher that fails,
    is logged as a warning and this returns, leaving the loop to its periodic rescan.
    """
    try:
        async for changes in awatch(captures_dir, recursive=True):
            if any(change == Change.added for change, _ in changes):
                wake()
    except Exception as e:
        log.warning("not watching %s (%s: %s); relying on the periodic rescan",
                    captures_dir, type(e).__name__, e)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vitality-observer")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run", help="observe every interaction, until stopped")
    sub.add_parser("status", help="show how many interactions are observed, and the last API call")
    return parser


async def _serve_until_stopped(config: ObserverConfig, observer: Observer) -> None:
    """`serve`, until SIGTERM (`docker stop`) or SIGINT (Ctrl-C) cancels it; that is a normal end."""
    task = asyncio.create_task(serve(config, observer))
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, task.cancel)
    try:
        await task
    except asyncio.CancelledError:
        pass
