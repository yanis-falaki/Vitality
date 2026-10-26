"""Running other programs for the commands that need to: the two ways, and the types that
let tests replace them.

`capture` runs a program and returns what it printed (for `git` questions); `stream` runs it
with its output going straight to the terminal (for `install.sh` and `doctor`, which the
person should watch). A command takes both as arguments, so tests run neither.
"""

import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

Capture = Callable[[Sequence[str], Path | None], tuple[int, str]]
"""Run `args` in `cwd` (None: here); return (exit code, its stdout, stripped; with stderr added when it failed)."""

Stream = Callable[[Sequence[str], Path | None], int]
"""Run `args` in `cwd` (None: here), output and input on the terminal; return the exit code."""

NOT_FOUND = 127
"""The exit code reported when the program could not be started at all (shell convention)."""


def capture(args: Sequence[str], cwd: Path | None) -> tuple[int, str]:
    """The real `Capture`; a program that cannot be started gives (127, the reason)."""
    try:
        result = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    except OSError as exc:
        return NOT_FOUND, str(exc)
    # On failure the reason is on stderr, so keep both; on success only what it answered.
    text = result.stdout + result.stderr if result.returncode else result.stdout
    return result.returncode, text.strip()


def stream(args: Sequence[str], cwd: Path | None) -> int:
    """The real `Stream`; a program that cannot be started gives 127, after saying why on stderr."""
    # Our own lines first: Python buffers them when output goes to a pipe or file, and the
    # child writes straight through, so without this its output would come out first.
    sys.stdout.flush()
    sys.stderr.flush()
    try:
        return subprocess.run(args, cwd=cwd).returncode
    except OSError as exc:
        print(f"cannot run {args[0]}: {exc}", file=sys.stderr)
        return NOT_FOUND
