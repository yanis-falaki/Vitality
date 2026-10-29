"""The observer's settings, read from environment variables.

Variable                        Default     Meaning
OBSERVER_CAPTURES_DIR           /captures   The server's interactions, read-only. If set, must not be empty.
OBSERVER_MODEL_DIR              /model      The user model; observations/ lives here. If set, must not be empty.
OBSERVER_STATE_DIR              /state      Holds calls.jsonl and skipped.txt. If set, must not be empty.
OBSERVER_BATCH_SIZE             10          Most interactions handed to one Claude call; a positive integer.
OBSERVER_CONTEXT_SIZE           15          Earlier observations of the same device given as context;
                                            a positive integer.
OBSERVER_CALL_TIMEOUT_SECONDS   600         A Claude call running longer is killed; a positive, finite number.
OBSERVER_MODEL                  (none)      Model passed to Claude Code; empty or unset means its default.

A numeric variable that is unset or empty keeps the default of `ObserverConfig`. Other
variables are ignored.
"""

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ObserverConfig:
    captures_dir: Path = Path("/captures")      # the server's interactions, read-only
    model_dir: Path = Path("/model")            # the user model; observations/ lives here
    state_dir: Path = Path("/state")            # holds calls.jsonl and skipped.txt
    batch_size: int = 10                # most interactions handed to one Claude call
    context_size: int = 15              # earlier observations of the same device given as context
    call_timeout_seconds: float = 600   # a Claude call running longer is killed
    model: str | None = None            # None: Claude Code's default


_DIRECTORIES = {
    "OBSERVER_CAPTURES_DIR": "captures_dir",
    "OBSERVER_MODEL_DIR": "model_dir",
    "OBSERVER_STATE_DIR": "state_dir",
}
_COUNTS = {
    "OBSERVER_BATCH_SIZE": "batch_size",
    "OBSERVER_CONTEXT_SIZE": "context_size",
}


def load_observer_config(environ: Mapping[str, str] = os.environ) -> ObserverConfig:
    """Read the settings from `environ` (the process environment by default).

    Raises ValueError, naming the variable, for a directory variable that is set but empty,
    a count that is not a positive integer, or a timeout that is not a positive, finite number.
    """
    values: dict[str, Path | int | float | str | None] = {}
    for name, field in _DIRECTORIES.items():
        directory = environ.get(name)
        if directory is not None:
            if not directory:
                raise ValueError(f"{name} must be a non-empty path, got ''")
            values[field] = Path(directory)
    for name, field in _COUNTS.items():
        text = environ.get(name, "").strip()
        if text:
            try:
                count = int(text)
            except ValueError:
                raise ValueError(f"{name} must be an integer, got {text!r}") from None
            if count < 1:
                raise ValueError(f"{name} must be positive, got {text!r}")
            values[field] = count
    text = environ.get("OBSERVER_CALL_TIMEOUT_SECONDS", "").strip()
    if text:
        try:
            seconds = float(text)
        except ValueError:
            raise ValueError(f"OBSERVER_CALL_TIMEOUT_SECONDS must be a number, got {text!r}") from None
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError(f"OBSERVER_CALL_TIMEOUT_SECONDS must be positive and finite, got {text!r}")
        values["call_timeout_seconds"] = seconds
    model = environ.get("OBSERVER_MODEL", "").strip()
    if model:
        values["model"] = model
    return ObserverConfig(**values)  # pyright: ignore[reportArgumentType]  # types validated above
