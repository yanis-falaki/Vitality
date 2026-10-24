"""`vitality-clientd`: the daemon's entry point (what the systemd service runs).

Picks the concrete parts — the portal frame source and the evdev activity watcher —
and runs a CaptureDaemon with them until stopped.
"""

import argparse
import asyncio
import logging
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from vitality_client.shared.config import load_config, resolve_paths
from vitality_client.shared.state import StateFile


def main(argv: Sequence[str] | None = None) -> int:
    argparse.ArgumentParser(
        prog="vitality-clientd",
        description="Take before/after screenshots around keyboard and mouse activity. "
                    "Control it with vitality-client.",
    ).parse_args(argv)
    session = os.environ.get("XDG_SESSION_TYPE", "")
    if session != "wayland":
        print(f"vitality-clientd requires a Wayland session (XDG_SESSION_TYPE={session})",
              file=sys.stderr)
        return 1
    try:
        paths = resolve_paths(os.environ, Path.home())
        config = load_config(paths.config_file)
    except (RuntimeError, ValueError) as exc:  # no runtime dir, bad config
        print(f"vitality-clientd: {exc}", file=sys.stderr)
        return 1
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(levelname)s %(name)s: %(message)s")
    # Imported here: they load GStreamer and evdev, which --help doesn't need.
    from vitality_client.daemon.activity import ActivityWatcher
    from vitality_client.daemon.daemon import CaptureDaemon, Daemon
    from vitality_client.daemon.frames.portal import PortalFrameSource

    token = StateFile(paths.state_file).load().restore_token

    async def run() -> None:
        # The daemon owns the state file; the frame source reports new tokens through it.
        holder: list[Daemon] = []
        frames = PortalFrameSource(token, lambda t: holder[0].save_restore_token(t))
        daemon = CaptureDaemon(config, paths, frames, ActivityWatcher)
        holder.append(daemon)
        await daemon.run()

    try:
        asyncio.run(run())
    except RuntimeError as exc:  # e.g. another daemon is already running
        print(f"vitality-clientd: {exc}", file=sys.stderr)
        return 1
    return 0
