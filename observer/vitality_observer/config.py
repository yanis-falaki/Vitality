"""The observer's settings, read from environment variables.

Variable                        Default             Meaning
OBSERVER_CAPTURES_DIR           /captures           The server's interactions, read-only. If set, must not be empty.
OBSERVER_MODEL_DIR              /model              The user model; observations/ and operations/ live here.
                                                    If set, must not be empty.
OBSERVER_STATE_DIR              /state              Holds calls.jsonl and skipped.txt. If set, must not be empty.
OBSERVER_MODE                   direct              `direct` (one API call per interaction, answered at once) or
                                                    `batch` (the same calls through the Batches API).
OBSERVER_PARALLEL               4                   Most interactions observed at the same time; a positive integer.
OBSERVER_MODEL                  claude-sonnet-5-5   Model used for the calls; empty or unset keeps the default.
OBSERVER_EFFORT                 low                 Effort of the calls: low, medium, high, xhigh or max.
OBSERVER_CALL_TIMEOUT_SECONDS   600                 A call running longer is given up on; a positive, finite number.

A variable that is unset or empty (except a directory) keeps the default of `ObserverConfig`.
Other variables are ignored.
"""

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

Mode = Literal["direct", "batch"]
Effort = Literal["low", "medium", "high", "xhigh", "max"]


@dataclass(frozen=True)
class ObserverConfig:
    captures_dir: Path = Path("/captures")      # the server's interactions, read-only
    model_dir: Path = Path("/model")            # the user model; observations/ and operations/ live here
    state_dir: Path = Path("/state")            # holds calls.jsonl and skipped.txt
    mode: Mode = "direct"               # one API call per interaction, or the same calls as a batch
    parallel: int = 4                   # most interactions observed at the same time
    model: str = "claude-sonnet-5-5"    # model used for the calls
    effort: Effort = "low"              # how hard the model thinks
    call_timeout_seconds: float = 600   # a call running longer is given up on


_DIRECTORIES = {
    "OBSERVER_CAPTURES_DIR": "captures_dir",
    "OBSERVER_MODEL_DIR": "model_dir",
    "OBSERVER_STATE_DIR": "state_dir",
}
_CHOICES = {
    "OBSERVER_MODE": ("mode", get_args(Mode)),
    "OBSERVER_EFFORT": ("effort", get_args(Effort)),
}


def load_observer_config(environ: Mapping[str, str] = os.environ) -> ObserverConfig:
    """Read the settings from `environ` (the process environment by default).

    Raises ValueError, naming the variable, for a directory variable that is set but empty,
    a mode or effort that is not one of the allowed values, a parallelism that is not a
    positive integer, or a timeout that is not a positive, finite number.
    """
    values: dict[str, Path | int | float | str] = {}
    for name, field in _DIRECTORIES.items():
        directory = environ.get(name)
        if directory is not None:
            if not directory:
                raise ValueError(f"{name} must be a non-empty path, got ''")
            values[field] = Path(directory)
    for name, (field, allowed) in _CHOICES.items():
        text = environ.get(name, "").strip()
        if text:
            if text not in allowed:
                raise ValueError(f"{name} must be one of {', '.join(allowed)}, got {text!r}")
            values[field] = text
    text = environ.get("OBSERVER_PARALLEL", "").strip()
    if text:
        try:
            count = int(text)
        except ValueError:
            raise ValueError(f"OBSERVER_PARALLEL must be an integer, got {text!r}") from None
        if count < 1:
            raise ValueError(f"OBSERVER_PARALLEL must be positive, got {text!r}")
        values["parallel"] = count
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
