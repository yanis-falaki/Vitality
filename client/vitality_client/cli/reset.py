"""`vitality-client reset`: delete the captures directly, with the daemon stopped."""

import shutil
import sys
from collections.abc import Mapping
from pathlib import Path

from vitality_client.cli.common import DAEMON_RUNNING, daemon_is_running, format_bytes, paths_from


# == Contract =============================================================================

def reset(env: Mapping[str, str], include_state: bool, assume_yes: bool) -> int:
    """Delete the captures directory (and, with `include_state`, the state directory),
    after listing what goes and asking for a typed `yes` unless `assume_yes`.

    Refuses while the daemon is running. Returns 0 when deleted or when there is nothing
    to delete; 1 when the daemon is running or the answer was not `yes` (nothing is
    deleted). Raises RuntimeError if XDG_RUNTIME_DIR is not set.
    """
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


# == Implementation =======================================================================

def _describe_data(data_dir: Path) -> str:
    captures = [p for p in (data_dir / "captures").rglob("*.webp") if p.is_file()]
    size = sum(p.stat().st_size for p in captures)
    return f"{data_dir}  ({len(captures)} captures, {format_bytes(size)})"
