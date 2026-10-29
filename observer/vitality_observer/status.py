"""`vitality-observer status`: how far the observer has got, in one line."""

import json
import sys
from datetime import datetime
from pathlib import Path

from vitality_observer.config import ObserverConfig
from vitality_observer.work import interactions, read_skipped, unobserved

DETAIL_LIMIT = 60       # longest failure detail shown, in characters
TAIL_BYTES = 64 * 1024  # how much of the end of `calls.jsonl` is read to find its last line


def status(config: ObserverConfig) -> int:
    """Print one line saying how many interactions are observed, how many were skipped, and
    how the last Claude call went; return the exit code.

        observer: 560 / 595 observed · 2 skipped · last call 21:28:26 EDT ok (40 s)

    `observed` is the interactions with a non-empty observation, out of all in the captures;
    `skipped` is the entries in `skipped.txt`. The last call is the last line of `calls.jsonl`,
    shown in local time, as `HH:MM:SS EDT` if today, else `YYYY-MM-DD HH:MM EDT`, then `ok` or
    `failed: <detail>` (the detail cut to 60 characters), then its duration in whole seconds.
    With no call logged it says `no calls yet`; with a last line that cannot be understood,
    `last call unreadable`. Changes nothing on disk.

    Returns 0, or 1 if the captures folder, `skipped.txt` or `calls.jsonl` cannot be read
    (`vitality-observer: cannot read <path>: <reason>` on stderr, nothing on stdout).
    A missing `skipped.txt` or `calls.jsonl` is not an error.
    """
    path = config.captures_dir
    try:
        list(path.iterdir())    # a missing or unreadable captures folder is an error here, not "nothing yet"
        total = len(interactions(config))
        observed = total - len(unobserved(config, {}))
        path = config.state_dir / "skipped.txt"
        skipped = len(read_skipped(path))
        path = config.state_dir / "calls.jsonl"
        last_call = _last_call_text(path, _now())
    except OSError as exc:
        print(f"vitality-observer: cannot read {path}: {exc.strerror or exc}", file=sys.stderr)
        return 1
    print(f"observer: {observed} / {total} observed · {skipped} skipped · {last_call}")
    return 0


def _now() -> datetime:
    return datetime.now().astimezone()


def _last_call_text(path: Path, now: datetime) -> str:
    """`last call <when> <outcome> (<n> s)`, `no calls yet`, or `last call unreadable`.
    Raises OSError if the file exists but cannot be read."""
    line = _last_line(path)
    if line is None:
        return "no calls yet"
    try:
        record = json.loads(line)
        when = _format_time(record["time"], now)
        outcome = "ok" if record["ok"] is True else f"failed: {_short(record.get('detail'))}"
        seconds = round(record["duration_ms"] / 1000)
    except (ValueError, KeyError, TypeError, AttributeError):
        return "last call unreadable"
    return f"last call {when} {outcome} ({seconds} s)"


def _last_line(path: Path) -> str | None:
    """The last non-empty line of the file, reading only its final `TAIL_BYTES`; None if the
    file is missing or has no such line."""
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - TAIL_BYTES))
            tail = f.read()
    except FileNotFoundError:
        return None
    for line in reversed(tail.decode("utf-8", errors="replace").splitlines()):
        if line.strip():
            return line
    return None


def _format_time(iso_ts: str, now: datetime) -> str:
    when = datetime.fromisoformat(iso_ts).astimezone()
    if when.date() == now.astimezone().date():
        return when.strftime("%H:%M:%S %Z")
    return when.strftime("%Y-%m-%d %H:%M %Z")


def _short(detail: object) -> str:
    text = " ".join(str(detail or "").split())
    return text if len(text) <= DETAIL_LIMIT else text[:DETAIL_LIMIT - 1] + "…"
