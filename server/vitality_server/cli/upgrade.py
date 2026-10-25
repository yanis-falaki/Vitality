"""`vitality-server upgrade`: pull the latest `main` from GitHub, reinstall, then run doctor.

Four steps, each printed under a `==> <step>` header like install.sh's. Any step that
cannot go on raises `UpgradeError`; `upgrade` reports it on stderr and exits 1, having
installed nothing unless the failing step is the install itself.
"""

import os
import sys
from pathlib import Path

from vitality_server.cli.commands import Capture, Stream, capture as run_capture, stream as run_stream
from vitality_server.cli.paths import PROGRAM_DIR, SOURCE_FILE
from vitality_server.shared.config import DEFAULT_CONFIG

REPO_URL = "https://github.com/yanis-falaki/Vitality.git"
FRESH_CLONE = f"git clone {REPO_URL} ~/Vitality && cd ~/Vitality && ./server/install.sh"
"""What to run when there is no usable checkout to upgrade from."""

INSTALL_COMMAND = "./server/install.sh"
"""Run from the checkout's root."""

DOCTOR_COMMAND = PROGRAM_DIR / "bin" / "vitality-server"
"""The freshly installed program: doctor must run from it, not from the old code in memory."""


class UpgradeError(Exception):
    """A step cannot go on; the message says why and what to do."""


def upgrade(config_path: Path, capture: Capture = run_capture, stream: Stream = run_stream) -> int:
    """Run the four steps; return the exit code.

    1. find the checkout `install.sh` last installed from (`SOURCE_FILE`);
    2. fetch `origin/main` and fast-forward the checkout to it. It must be on `main` with no
       modified tracked files. With nothing new it prints `already up to date (<sha>)` and
       stops with 0: nothing is installed;
    3. run `./server/install.sh` there, output on the terminal;
    4. run `doctor` as a fresh process of the newly installed program (with `--config` when
       `config_path` is not the default); its exit code is the result.

    Refuses to run as root: git and install.sh must use the person's own account (install.sh
    uses sudo itself). Returns 1, with the reason on stderr, when a step fails.
    """
    try:
        _refuse_root()
        print("==> finding the checkout")
        checkout = _find_checkout(capture)
        print("==> pulling main")
        if not _pull(checkout, capture):
            return 0
        print("==> installing")
        _install(checkout, stream)
    except UpgradeError as exc:
        print(f"vitality-server: {exc}", file=sys.stderr)
        return 1
    print("==> checking")
    return stream(_doctor_command(config_path), None)


def _refuse_root() -> None:
    if os.geteuid() == 0:
        raise UpgradeError("run vitality-server upgrade as your normal user, not with sudo "
                           "(git and install.sh use your account; install.sh uses sudo itself)")


def _find_checkout(capture: Capture) -> Path:
    try:
        recorded = SOURCE_FILE.read_text().strip()
    except OSError:
        recorded = ""
    if not recorded:
        raise UpgradeError("don't know where the repo is: run ./server/install.sh once from a git "
                           f"clone of the repo (it records the path in {SOURCE_FILE})")
    checkout = Path(recorded)
    code, _ = capture(["git", "-C", str(checkout), "rev-parse", "--is-inside-work-tree"], None)
    if code != 0:
        raise UpgradeError(f"{checkout} is not a git checkout any more. Clone the repo again "
                           f"and install from it:\n  {FRESH_CLONE}")
    print(f"    {checkout}")
    return checkout


def _pull(checkout: Path, capture: Capture) -> bool:
    """Bring the checkout up to `origin/main`; False if it already was."""
    def git(*args: str) -> tuple[int, str]:
        return capture(["git", "-C", str(checkout), *args], None)

    _, branch = git("rev-parse", "--abbrev-ref", "HEAD")
    if branch != "main":
        raise UpgradeError(f"the checkout is on '{branch}', not main; nothing was installed. "
                           f"Run: git -C {checkout} switch main")
    _, modified = git("status", "--porcelain", "--untracked-files=no")
    if modified:
        raise UpgradeError(f"the checkout has uncommitted changes to tracked files; nothing was "
                           f"installed. Look with: git -C {checkout} status")
    code, output = git("fetch", "--quiet", "origin", "main")
    if code != 0:
        raise UpgradeError(f"git fetch failed: {output}")
    _, listing = git("log", "--oneline", "HEAD..origin/main")
    if not listing:
        _, sha = git("rev-parse", "--short", "HEAD")
        print(f"already up to date ({sha})")
        return False
    new_commits = listing.splitlines()
    print(f"{len(new_commits)} new commit(s):")
    for line in new_commits:
        print(f"  {line}")
    code, _ = git("merge", "--quiet", "--ff-only", "origin/main")
    if code != 0:
        raise UpgradeError("the checkout has diverged from origin/main; nothing was installed")
    return True


def _install(checkout: Path, stream: Stream) -> None:
    code = stream([INSTALL_COMMAND], checkout)
    if code != 0:
        raise UpgradeError(f"install.sh failed (exit {code}); see its output above")


def _doctor_command(config_path: Path) -> list[str]:
    command = [str(DOCTOR_COMMAND), "doctor"]
    if config_path != DEFAULT_CONFIG:
        command += ["--config", str(config_path)]
    return command
