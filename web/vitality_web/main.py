"""The `vitality-web` command.

    vitality-web serve [--model /model] [--state /state] [--captures /captures] [--app DIR]
    vitality-web healthcheck
    vitality-web openapi

`serve` runs the app with uvicorn on 0.0.0.0, port `WEB_PORT` (default 4000), until stopped. `--app` is
the built app (`web/app/dist`; in the image, /opt/vitality-web/app). `healthcheck` asks the running
server for /api/health and exits 0 if it answers. `openapi` prints the API's OpenAPI schema, from
which the app's types are generated (see README.md). A bad `WEB_PORT` or argument exits 2.
"""

import argparse
import json
import os
import sys
import urllib.request
from collections.abc import Mapping, Sequence
from pathlib import Path

from vitality_web.app import create_app

DEFAULT_PORT = 4000


def main(argv: Sequence[str] | None = None, environ: Mapping[str, str] = os.environ) -> int:
    parser = argparse.ArgumentParser(prog="vitality-web")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="serve the app and its API until stopped")
    serve.add_argument("--model", type=Path, default=Path("/model"), help="the model folder (default /model)")
    serve.add_argument("--state", type=Path, default=Path("/state"), help="the modeler's records (default /state)")
    serve.add_argument("--captures", type=Path, default=Path("/captures"), help="the screenshots (default /captures)")
    serve.add_argument("--app", type=Path, default=Path("/opt/vitality-web/app"), help="the built app")
    commands.add_parser("healthcheck", help="exit 0 if the server answers")
    commands.add_parser("openapi", help="print the API's OpenAPI schema")
    try:
        args = parser.parse_args(argv)
        port = web_port(environ)
    except SystemExit as stop:
        return stop.code if isinstance(stop.code, int) else 2
    except ValueError as e:
        print(f"vitality-web: {e}", file=sys.stderr)
        return 2
    if args.command == "openapi":
        app = create_app(Path("/model"), Path("/state"), Path("/captures"), None)
        print(json.dumps(app.openapi(), indent=2))
        return 0
    if args.command == "healthcheck":
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=10) as reply:
                return 0 if reply.status == 200 else 1
        except OSError as e:
            print(f"vitality-web: not answering: {e}", file=sys.stderr)
            return 1
    import uvicorn

    uvicorn.run(create_app(args.model, args.state, args.captures, args.app), host="0.0.0.0", port=port,
                log_level="info", access_log=False)
    return 0


def web_port(environ: Mapping[str, str]) -> int:
    """`WEB_PORT`, or 4000 when unset or empty. Raises ValueError if it is not a port number."""
    value = environ.get("WEB_PORT", "").strip()
    if not value:
        return DEFAULT_PORT
    if not value.isdigit() or not 1 <= int(value) <= 65535:
        raise ValueError(f"WEB_PORT must be an integer from 1 to 65535, got {value!r}")
    return int(value)
