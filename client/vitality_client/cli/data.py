"""Commands that work on the captures directly, with the daemon stopped: capture-once, reset."""

import asyncio
import shutil
import sys
import time
from collections.abc import Mapping
from pathlib import Path

import ulid

from vitality_client.cli.common import DAEMON_RUNNING, daemon_is_running, format_bytes, paths_from, require_wayland
from vitality_client.config import load_config
from vitality_client.control import State, StateFile

FIRST_FRAME_TIMEOUT = 10.0


def capture_once(env: Mapping[str, str]) -> int:
    if not require_wayland(env):
        return 1
    paths = paths_from(env)
    # Portal restore tokens are single-use: capture-once would spend the daemon's token.
    if daemon_is_running(paths.socket_path):
        print(DAEMON_RUNNING, file=sys.stderr)
        return 1
    from vitality_client.frames.portal import PortalFrameSource
    from vitality_client.storage import Store, Writer

    config = load_config(paths.config_file)
    state_file = StateFile(paths.state_file)

    def save_token(token: str) -> None:
        state = state_file.load()
        state_file.save(State(state.paused, state.paused_until, token))

    async def main() -> Path | None:
        frames = PortalFrameSource(state_file.load().restore_token, save_token)
        try:
            await frames.start()  # may wait on the permission dialog
            deadline = time.monotonic() + FIRST_FRAME_TIMEOUT
            while (frame := frames.latest_frame()) is None:
                if time.monotonic() >= deadline:
                    return None
                await asyncio.sleep(0.1)
        finally:
            await frames.close()
        store = Store(paths.data_dir / "index.sqlite")
        try:
            writer = Writer(paths.data_dir, store, config.webp_quality, lambda: str(ulid.ULID()))
            row = writer.write(frame, "after", str(ulid.ULID()), time.time())
        finally:
            store.close()
        return (paths.data_dir / row.path).resolve()

    path = asyncio.run(main())
    if path is None:
        print("no frame received from the screen capture portal", file=sys.stderr)
        return 1
    print(path)
    return 0


def _describe_data(data_dir: Path) -> str:
    captures = [p for p in (data_dir / "captures").rglob("*.webp") if p.is_file()]
    size = sum(p.stat().st_size for p in captures)
    return f"{data_dir}  ({len(captures)} captures, {format_bytes(size)})"


def reset(env: Mapping[str, str], include_state: bool, assume_yes: bool) -> int:
    paths = paths_from(env)
    if daemon_is_running(paths.socket_path):
        print(DAEMON_RUNNING, file=sys.stderr)
        return 1
    targets = []
    if paths.data_dir.exists():
        targets.append((paths.data_dir, _describe_data(paths.data_dir)))
    state_dir = paths.state_file.parent
    if include_state and state_dir.exists():
        targets.append((state_dir, f"{state_dir}  (pause state and screen-sharing permission)"))
    if not targets:
        print("nothing to delete")
        return 0
    print("This permanently deletes:")
    for _, description in targets:
        print(f"  {description}")
    if not assume_yes:
        try:
            reply = input("Type 'yes' to continue: ")
        except EOFError:
            reply = ""
        if reply.strip() != "yes":
            print("aborted, nothing deleted", file=sys.stderr)
            return 1
    for path, _ in targets:
        shutil.rmtree(path)
        print(f"deleted {path}")
    return 0
