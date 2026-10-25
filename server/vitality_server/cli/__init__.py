"""The `vitality-server` command line: looks at, and upgrades, the server (`vitality-serverd`) on this machine.

    status    what each device has uploaded, the free disk space and where things live
                                                                  status.py
    doctor    check the setup, one line per check                 doctor.py (+ checks.py)
    upgrade   pull the latest main from GitHub, reinstall,        upgrade.py (+ commands.py)
              then run doctor

`main(argv) -> exit code` is the entry point; `main.py` parses arguments and dispatches. The
block of paths `status` and `doctor` end with is in `paths.py`; `display.py` formats sizes and
times; `commands.py` runs other programs (git, install.sh) so tests can replace it. `status`
and `doctor` change nothing on disk; `upgrade` reinstalls the server.
"""

from vitality_server.cli.main import main

__all__ = ["main"]
