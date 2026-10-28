"""The observer's settings, read from a TOML file.

Recognised keys: `captures_dir`, `model_dir`, `state_dir`, `batch_size`, `context_size`,
`call_timeout_seconds`, `model`. Anything omitted keeps the default of `ObserverConfig`.
`DEFAULT_CONFIG` is where the observer looks for the file when `--config` is not given.
"""

import math
import tomllib
from dataclasses import dataclass
from pathlib import Path


DEFAULT_CONFIG = Path("/etc/vitality/observer.toml")
"""The settings file used when `--config` is not given."""


@dataclass(frozen=True)
class ObserverConfig:
    captures_dir: Path = Path("/srv/vitality/captures")     # the server's interactions, read-only
    model_dir: Path = Path("/srv/vitality-model")           # the user model; observations/ lives here
    state_dir: Path = Path("/var/lib/vitality-librarian")   # holds calls.jsonl and skipped.txt
    batch_size: int = 10                # most interactions handed to one Claude call
    context_size: int = 15              # earlier observations of the same device given as context
    call_timeout_seconds: float = 600   # a Claude call running longer is killed
    model: str | None = None            # None: Claude Code's default


_PATH_KEYS = ("captures_dir", "model_dir", "state_dir")
_COUNT_KEYS = ("batch_size", "context_size")
_KEYS = (*_PATH_KEYS, *_COUNT_KEYS, "call_timeout_seconds", "model")


def load_observer_config(path: Path) -> ObserverConfig:
    """Load settings from a TOML file; a missing file yields the defaults.

    The directories and `model` must be non-empty strings, `batch_size` and `context_size`
    positive integers, and `call_timeout_seconds` a positive, finite number.

    Raises ValueError, naming the key, for an unknown key or a value of the wrong type or
    range, and naming the file and the line for a file that is not valid TOML. A file that
    cannot be read raises OSError.
    """
    try:
        with open(path, "rb") as f:
            raw = tomllib.load(f)
    except FileNotFoundError:
        return ObserverConfig()
    except tomllib.TOMLDecodeError as e:
        raise ValueError(f"{path} is not valid TOML: {e}") from e

    unknown = [key for key in raw if key not in _KEYS]
    if unknown:
        raise ValueError(f"unknown config key: {unknown[0]}")

    values: dict[str, Path | int | float | str] = {}
    for key in _PATH_KEYS:
        if key in raw:
            directory = raw[key]
            if not isinstance(directory, str) or not directory:
                raise ValueError(f"config key {key} must be a non-empty path, got {directory!r}")
            values[key] = Path(directory)
    for key in _COUNT_KEYS:
        if key in raw:
            count = raw[key]
            if not isinstance(count, int) or isinstance(count, bool):
                raise ValueError(f"config key {key} must be an integer, got {count!r}")
            if count < 1:
                raise ValueError(f"config key {key} must be positive, got {count!r}")
            values[key] = count
    if "call_timeout_seconds" in raw:
        seconds = raw["call_timeout_seconds"]
        if not isinstance(seconds, (int, float)) or isinstance(seconds, bool):
            raise ValueError(f"config key call_timeout_seconds must be a number, got {seconds!r}")
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError(f"config key call_timeout_seconds must be positive and finite, got {seconds!r}")
        values["call_timeout_seconds"] = float(seconds)
    if "model" in raw:
        model = raw["model"]
        if not isinstance(model, str) or not model:
            raise ValueError(f"config key model must be a non-empty string, got {model!r}")
        values["model"] = model
    return ObserverConfig(**values)  # pyright: ignore[reportArgumentType]  # types validated above
