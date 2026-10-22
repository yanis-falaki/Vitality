"""`vitality-client run`: start the daemon (what the systemd service runs)."""

import asyncio
import logging
import sys
from collections.abc import Mapping

from vitality_client.cli.common import paths_from, require_wayland
from vitality_client.config import load_config
from vitality_client.control import StateFile


def run(env: Mapping[str, str]) -> int:
    if not require_wayland(env):
        return 1
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(levelname)s %(name)s: %(message)s")
    # Imported here: these pull in GStreamer and evdev, which status/pause/doctor don't need.
    from vitality_client.activity import ActivityWatcher
    from vitality_client.daemon import Daemon
    from vitality_client.frames.portal import PortalFrameSource

    paths = paths_from(env)
    config = load_config(paths.config_file)
    token = StateFile(paths.state_file).load().restore_token

    async def main() -> None:
        # The daemon owns the state file; the frame source reports new tokens through it.
        holder: list[Daemon] = []
        frames = PortalFrameSource(token, lambda t: holder[0].save_restore_token(t))
        daemon = Daemon(config, paths, frames, ActivityWatcher)
        holder.append(daemon)
        await daemon.run()

    asyncio.run(main())
    return 0
