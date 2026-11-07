"""Serve the fixture model for looking at the app and for the smoke run (never the owner's data).

    uv run python tests/serve_fixture.py <folder> [--port 4000] [--app app/dist] [--large [--days 120]]
                                         [--now 2026-11-04T23:00:00-05:00]

Builds the fixture (with `--large`, about four months of model: `fixture_large.py`) into `<folder>`
(emptied first), holds the modeler's scheduler lock so the top
bar reads `idle` as with a running modeler, and serves it on 127.0.0.1 until stopped. `--now` is the
time the API gives as the present (`/api/pipeline`), so that Home reads as on that day; by default,
the clock.
"""

import argparse
import fcntl
import shutil
import sys
from datetime import datetime
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
    parser.add_argument("--now", type=datetime.fromisoformat, help="the present, as ISO 8601 with an offset")
    args = parser.parse_args()
    if args.now is not None and args.now.tzinfo is None:
        parser.error("--now needs an offset, e.g. 2026-11-04T23:00:00-05:00")
    shutil.rmtree(args.folder, ignore_errors=True)
    fx = build_large(args.folder, args.days) if args.large else build(args.folder)
    with open(fx.state / "scheduler.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        uvicorn.run(create_app(fx.model, fx.state, fx.captures, args.app, now=args.now), host="127.0.0.1", port=args.port,
                    log_level="warning")


if __name__ == "__main__":
    main()
