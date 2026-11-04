"""The modeler's settings, read from environment variables.

Variable                      Default           Meaning
MODELER_MODEL                 claude-opus-5-5   Model of the Claude Code session.
MODELER_EFFORT                high              Its effort: low, medium, high, xhigh or max.
MODELER_MAX_OPERATIONS        500               Most operations offered to one run; a positive integer.
                                                Whole operations files are offered, so a run may exceed it
                                                when the oldest file alone is larger.
MODELER_RUN_TIMEOUT_SECONDS   3600              A run lasting longer is given up on; a positive, finite number.

A variable that is unset or empty keeps the default of `ModelerConfig`. Other variables are ignored.
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, get_args

Effort = Literal["low", "medium", "high", "xhigh", "max"]


@dataclass(frozen=True)
class ModelerConfig:
    model: str = "claude-opus-5-5"      # model of the session
    effort: Effort = "high"             # how hard the model thinks
    max_operations: int = 500           # most operations offered to one run
    run_timeout_seconds: float = 3600   # a run lasting longer is given up on


SETTINGS = ("model", "effort", "max_operations", "run_timeout_seconds")
"""The fields of `ModelerConfig`; each is read from `MODELER_<NAME>`."""


def load_modeler_config(environ: Mapping[str, str]) -> ModelerConfig:
    """Read the settings from `environ`; empty or unset keeps the default.

    Raises ValueError, naming the variable, for an effort outside the five, a `max_operations`
    that is not a positive integer, or a timeout that is not a positive, finite number.
    """
    values: dict[str, str | int | float] = {}
    for setting in SETTINGS:
        name = f"MODELER_{setting.upper()}"
        text = environ.get(name, "").strip()
        if not text:
            continue
        if setting == "effort":
            if text not in get_args(Effort):
                raise ValueError(f"{name} must be one of {', '.join(get_args(Effort))}, got {text!r}")
            values[setting] = text
        elif setting == "max_operations":
            try:
                count = int(text)
            except ValueError:
                raise ValueError(f"{name} must be an integer, got {text!r}") from None
            if count < 1:
                raise ValueError(f"{name} must be positive, got {text!r}")
            values[setting] = count
        elif setting == "run_timeout_seconds":
            try:
                seconds = float(text)
            except ValueError:
                raise ValueError(f"{name} must be a number, got {text!r}") from None
            if not math.isfinite(seconds) or seconds <= 0:
                raise ValueError(f"{name} must be positive and finite, got {text!r}")
            values[setting] = seconds
        else:
            values[setting] = text
    return ModelerConfig(**values)  # pyright: ignore[reportArgumentType]  # types validated above
