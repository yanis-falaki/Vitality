"""The `vitality-client` command line.

    run            start the daemon (what the service runs)              run.py
    pause/resume/toggle/status/capture   talk to the running daemon     control.py
    doctor         check the setup; show whether capture is paused       doctor.py (+ checks.py)
    reset          delete the captures, with the daemon stopped          reset.py

`main(argv) -> exit code` is the entry point; `main.py` parses arguments and
dispatches. Shared paths, messages and formatting live in `common.py`.
"""

from vitality_client.cli.main import main

__all__ = ["main"]
