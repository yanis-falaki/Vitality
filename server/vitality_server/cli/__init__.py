"""The `vitality-server` command, the one program in the server's container.

    serve         run the server                                  (daemon/main.py)
    status        what each device has uploaded and the free disk space
                                                                  status.py
    healthcheck   exit 0 if the running server answers /health    healthcheck.py

`main(argv) -> exit code` is the entry point; `main.py` loads the settings from the
environment, parses arguments and dispatches, handing each command a `ServerConfig`.
`display.py` formats sizes and times. `status` and `healthcheck` change nothing on disk.
"""

from vitality_server.cli.main import main

__all__ = ["main"]
