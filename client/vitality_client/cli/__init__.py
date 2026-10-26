"""The `vitality-client` command line: controls the daemon (`vitality-clientd`).

    pause/resume/toggle/status/capture   talk to the running daemon     control.py
    doctor         check the setup; show whether capture is paused       doctor.py (+ checks.py)
    reset          delete the captures, with the daemon stopped          reset.py
    upgrade        reinstall from the repo's main, then run doctor       upgrade.py

`main(argv) -> exit code` is the entry point; `main.py` parses arguments and
dispatches. Shared paths, messages and formatting live in `common.py`; `duration.py` parses
`pause 30m`-style durations into seconds before they are sent to the daemon; `commands.py`
runs other programs (git, install.sh, the installed doctor) for `upgrade`.
"""

from vitality_client.cli.main import main

__all__ = ["main"]
