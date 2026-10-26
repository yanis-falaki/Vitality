"""`vitality-client upgrade`: reinstall the client from the repo's `main`, then run doctor.

Four steps, each printed under a `==> <step>` header like install.sh's. Any step that
cannot go on raises `UpgradeError`; `upgrade` reports it on stderr and exits 1, having
installed nothing unless the failing step is the install itself. Unlike the server's
upgrade it does not fetch or pull: you merge to `main` in the repo yourself.
"""

import os
import sys
from collections.abc import Mapping
from pathlib import Path

from vitality_client.cli.common import app_dir
from vitality_client.cli.commands import Capture, Stream, capture as run_capture, stream as run_stream

INSTALL_COMMAND = "./client/install.sh"
"""Run from the checkout's root."""

NOT_INSTALLED_FIX = "run ./client/install.sh from the repo"
"""What to do when there is no usable checkout to reinstall from."""


class UpgradeError(Exception):
    """A step cannot go on; the message says why and what to do."""


def upgrade(env: Mapping[str, str], capture: Capture = run_capture, stream: Stream = run_stream) -> int:
    """Run the four steps; return the exit code.

    1. find the checkout `install.sh` last installed from (`source` in the app directory);
    2. check that it is on `main` with no modified tracked files, and compare its HEAD with
       the commit recorded as installed (`installed`). With nothing new it prints
       `already installed (<sha>)` and stops with 0: nothing is installed;
    3. run `./client/install.sh` there, output on the terminal;
    4. run `doctor` as a fresh process of the newly installed program; its exit code is
       the result.

    Refuses to run as root: git and install.sh must use the person's own account. Returns 1,
    with the reason on stderr, when a step fails.
    """
    app = app_dir(env)
    try:
        _refuse_root()
        print("==> finding the checkout")
        checkout = _find_checkout(app / "source", capture)
        _require_clean_main(checkout, capture)
        print("==> comparing with the installed version")
        if not _list_changes(checkout, app / "installed", capture):
            return 0
        print("==> installing")
        _install(checkout, stream)
    except UpgradeError as exc:
        print(f"vitality-client: {exc}", file=sys.stderr)
        return 1
    print("==> checking")
    return stream([str(app / "venv/bin/vitality-client"), "doctor"], None)


def _refuse_root() -> None:
    if os.geteuid() == 0:
        raise UpgradeError("run vitality-client upgrade as your normal user, not with sudo "
                           "(it installs into your home)")


def _find_checkout(source_file: Path, capture: Capture) -> Path:
    try:
        recorded = source_file.read_text().strip()
    except OSError:
        recorded = ""
    if not recorded:
        raise UpgradeError("not installed with client/install.sh yet: "
                           f"{NOT_INSTALLED_FIX} (it records the path in {source_file})")
    checkout = Path(recorded)
    code, _ = capture(["git", "-C", str(checkout), "rev-parse", "--is-inside-work-tree"], None)
    if code != 0:
        raise UpgradeError(f"{checkout} is not a git checkout any more. Install again "
                           f"from the repo: {NOT_INSTALLED_FIX}")
    print(f"    {checkout}")
    return checkout


def _require_clean_main(checkout: Path, capture: Capture) -> None:
    _, branch = capture(["git", "-C", str(checkout), "rev-parse", "--abbrev-ref", "HEAD"], None)
    if branch != "main":
        raise UpgradeError(f"the checkout is on '{branch}', not main; nothing was installed. "
                           f"Run: git -C {checkout} switch main")
    _, modified = capture(["git", "-C", str(checkout), "status", "--porcelain", "--untracked-files=no"], None)
    if modified:
        raise UpgradeError(f"the checkout has uncommitted changes to tracked files; nothing was "
                           f"installed. Look with: git -C {checkout} status")


def _list_changes(checkout: Path, installed_file: Path, capture: Capture) -> bool:
    """Print what the install would bring; False if `main` is already installed."""
    def git(*args: str) -> tuple[int, str]:
        return capture(["git", "-C", str(checkout), *args], None)

    _, head = git("rev-parse", "HEAD")
    try:
        installed = installed_file.read_text().strip()
    except OSError:
        installed = ""
    if installed == head:
        _, sha = git("rev-parse", "--short", "HEAD")
        print(f"already installed ({sha})")
        return False
    ancestor = bool(installed) and git("merge-base", "--is-ancestor", installed, head)[0] == 0
    if ancestor:
        _, listing = git("log", "--oneline", f"{installed}..{head}")
        new_commits = listing.splitlines()
        print(f"installing {len(new_commits)} new commit(s):")
        for line in new_commits:
            print(f"  {line}")
    else:  # never recorded, or not part of this history: say only what is being installed
        _, latest = git("log", "--oneline", "-1", head)
        print(f"installing {latest}")
    return True


def _install(checkout: Path, stream: Stream) -> None:
    code = stream([INSTALL_COMMAND], checkout)
    if code != 0:
        raise UpgradeError(f"install.sh failed (exit {code}); see its output above")
