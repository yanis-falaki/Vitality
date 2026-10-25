"""`vitality-clientd`: the daemon's entry point (what the systemd service runs).

Picks the concrete parts — the portal frame source, the evdev activity watcher and, when
`upload_server` is configured, the HTTP uploader — and runs a CaptureDaemon with them until stopped.
"""

import argparse
import asyncio
import logging
import os
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from vitality_client.shared.config import load_config, resolve_paths
from vitality_client.shared.state import StateFile


def main(argv: Sequence[str] | None = None) -> int:
    argparse.ArgumentParser(
        prog="vitality-clientd",
        description="Record screenshots of each burst of keyboard and mouse activity as an "
                    "interaction folder, and upload each finished interaction to the server "
                    "set as upload_server, if any. Control it with vitality-client.",
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
    from vitality_client.daemon.daemon import CaptureDaemon
    from vitality_client.daemon.frames.portal import PortalFrameSource
    from vitality_client.daemon.storage import CaptureIndex
    from vitality_client.daemon.upload import HttpUploader, Uploader

    token = StateFile(paths.state_file).load().restore_token

    def make_uploader(server: str) -> Callable[[CaptureIndex], Uploader]:
        return lambda index: HttpUploader(server, index, paths.data_dir)

    async def run() -> None:
        daemon = CaptureDaemon(
            config, paths, PortalFrameSource(token), ActivityWatcher(),
            uploader=None if config.upload_server is None else make_uploader(config.upload_server),
        )
        await daemon.run()

    try:
        asyncio.run(run())
    except RuntimeError as exc:  # another daemon is already running, or OldLayoutError:
        # the data directory needs the one-time migration (nothing was touched)
        print(f"vitality-clientd: {exc}", file=sys.stderr)
        return 1
    return 0
