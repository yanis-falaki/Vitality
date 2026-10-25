"""Configuration and filesystem path resolution."""

from __future__ import annotations

import math
import tomllib
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Mapping

import httpx


@dataclass(frozen=True)
class Config:
    settle_seconds: float = 2
    max_gap_seconds: float = 10
    webp_quality: int = 85
    disk_warning_gb: float = 20
    upload_server: str | None = None


@dataclass(frozen=True)
class Paths:
    config_file: Path
    data_dir: Path
    state_file: Path
    socket_path: Path


def resolve_paths(env: Mapping[str, str], home: Path) -> Paths:
    """Resolve daemon paths from XDG variables, falling back to defaults under `home`."""

    def base(var: str, default: Path) -> Path:
        value = env.get(var)
        return Path(value) if value else default

    runtime = env.get("XDG_RUNTIME_DIR")
    if not runtime:
        raise RuntimeError("XDG_RUNTIME_DIR is not set")

    config_home = base("XDG_CONFIG_HOME", home / ".config")
    data_home = base("XDG_DATA_HOME", home / ".local/share")
    state_home = base("XDG_STATE_HOME", home / ".local/state")
    return Paths(
        config_file=config_home / "vitality/client.toml",
        data_dir=data_home / "vitality/client",
        state_file=state_home / "vitality/client/state.json",
        socket_path=Path(runtime) / "vitality/client.sock",
    )


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _check_url(key: str, value: object) -> str:
    """An http(s) URL with a host and, if given, a port in 1-65535; without a trailing /."""
    problem = f"config key {key} must be an http:// or https:// URL with a host"
    if not isinstance(value, str):
        raise ValueError(f"{problem}, got {value!r}")
    try:
        url = httpx.URL(value)
    except httpx.InvalidURL as exc:
        raise ValueError(f"{problem}, got {value!r} ({exc})") from None
    if url.scheme not in ("http", "https") or not url.host:
        raise ValueError(f"{problem}, got {value!r}")
    if url.port is not None and not 1 <= url.port <= 65535:
        raise ValueError(f"config key {key} has an invalid port {url.port}, got {value!r}")
    return value.rstrip("/")


def _check_range(key: str, value: float) -> None:
    if not math.isfinite(value):
        raise ValueError(f"config key {key} must be a finite number, got {value!r}")
    if key in ("settle_seconds", "max_gap_seconds") and value <= 0:
        raise ValueError(f"config key {key} must be greater than 0, got {value!r}")
    if key == "disk_warning_gb" and value < 0:
        raise ValueError(f"config key {key} must not be negative, got {value!r}")
    if key == "webp_quality" and not 1 <= value <= 100:
        raise ValueError(f"config key {key} must be between 1 and 100, got {value!r}")


def load_config(path: Path) -> Config:
    """Load config from a TOML file; a missing file yields defaults."""
    try:
        with open(path, "rb") as f:
            raw = tomllib.load(f)
    except FileNotFoundError:
        return Config()

    types = {f.name: f.type for f in fields(Config)}
    for key in raw:
        if key not in types:
            raise ValueError(f"unknown config key: {key}")

    values: dict[str, float | int | str] = {}
    for key, value in raw.items():
        if types[key] in (str, "str | None"):
            values[key] = _check_url(key, value)
            continue
        number: float
        if types[key] in (float, "float"):
            if not _is_number(value):
                raise ValueError(f"config key {key} must be a number, got {value!r}")
            number = float(value)
        else:
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError(f"config key {key} must be an integer, got {value!r}")
            number = value
        _check_range(key, number)
        values[key] = number
    return Config(**values)  # pyright: ignore[reportArgumentType]  # types validated above
