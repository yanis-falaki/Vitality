"""Serve the fixture model for looking at the app and for the smoke run (never the owner's data).

    uv run python tests/serve_fixture.py <folder> [--port 4000] [--app app/dist] [--large [--days 120]]

Builds the fixture (with `--large`, about four months of model: `fixture_large.py`) into `<folder>`
(emptied first), holds the modeler's scheduler lock so the top
bar reads `idle` as with a running modeler, and serves it on 127.0.0.1 until stopped.
"""

import argparse
import fcntl
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import uvicorn  # noqa: E402

from fixture import build  # noqa: E402
from fixture_large import build_large  # noqa: E402
from vitality_web.app import create_app  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("folder", type=Path)
    parser.add_argument("--port", type=int, default=4000)
    parser.add_argument("--app", type=Path, default=Path(__file__).parent.parent / "app" / "dist")
    parser.add_argument("--large", action="store_true", help="the large fixture (about four months)")
    parser.add_argument("--days", type=int, default=120, help="with --large: how many calendar days")
    args = parser.parse_args()
    shutil.rmtree(args.folder, ignore_errors=True)
    fx = build_large(args.folder, args.days) if args.large else build(args.folder)
    with open(fx.state / "scheduler.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        uvicorn.run(create_app(fx.model, fx.state, fx.captures, args.app), host="127.0.0.1", port=args.port,
                    log_level="warning")


if __name__ == "__main__":
    main()
