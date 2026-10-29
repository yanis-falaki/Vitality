"""`vitality-observerd [--config PATH]`: load the settings, then keep every interaction observed.

This is the observer itself, run by systemd as `vitality-librarian`. It runs the loop
(`vitality_observer.loop`) and a watcher on the captures that wakes the loop when something
new arrives.
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

from vitality_observer.claude import ClaudeCode, Observer
from vitality_observer.config import DEFAULT_CONFIG, ObserverConfig, load_observer_config
from vitality_observer.loop import ObserveLoop

log = logging.getLogger("vitality_observer")


def main(argv: Sequence[str] | None = None) -> int:
    """Run the observer until it is stopped; return the process exit code.

    `argv` is the argument list (default: `sys.argv[1:]`); `--config PATH` names the
    settings file. At startup, in order:

        1. Parse the arguments and set up logging to stderr.
        2. Load the settings (a missing file means the defaults).
        3. Refuse to run as root: the observer must run as `vitality-librarian`, so that
           the captures stay read-only to it and to the Claude Code it starts.
        4. Create `state_dir` if it is missing.
        5. Run `serve` with the real Claude Code until SIGTERM or Ctrl-C.

    Exit codes: 0 after running and stopping; 1 for unusable settings, running as root, or a
    `state_dir` that cannot be created (the reason goes to stderr). A bad command line exits
    2 (argparse).
    """
    parser = argparse.ArgumentParser(prog="vitality-observerd", description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG,
                        help=f"settings file (default: {DEFAULT_CONFIG})")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(levelname)s %(name)s: %(message)s")      # journald adds the time

    try:
        config = load_observer_config(args.config)
    except (ValueError, OSError) as e:
        print(f"vitality-observerd: {e}", file=sys.stderr)
        return 1
    if os.geteuid() == 0:
        print("vitality-observerd: refusing to run as root; run it as vitality-librarian", file=sys.stderr)
        return 1
    try:
        config.state_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(f"vitality-observerd: {e}", file=sys.stderr)
        return 1

    try:
        asyncio.run(_serve_until_stopped(config, ClaudeCode(config)))
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


async def _serve_until_stopped(config: ObserverConfig, observer: Observer) -> None:
    """`serve`, until SIGTERM (systemd's stop) or SIGINT (Ctrl-C) cancels it; that is a normal end."""
    task = asyncio.create_task(serve(config, observer))
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, task.cancel)
    try:
        await task
    except asyncio.CancelledError:
        pass
