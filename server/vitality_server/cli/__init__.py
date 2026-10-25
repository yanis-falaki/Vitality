"""The `vitality-server` command line: looks at the server (`vitality-serverd`) on this machine.

    status    what each device has uploaded, the free disk space and where things live
                                                                  status.py
    doctor    check the setup, one line per check                 doctor.py (+ checks.py)

`main(argv) -> exit code` is the entry point; `main.py` parses arguments and dispatches. The
block of paths both commands end with is in `paths.py`; `display.py` formats sizes and times.
Neither command changes anything on disk.
"""

from vitality_server.cli.main import main

__all__ = ["main"]
