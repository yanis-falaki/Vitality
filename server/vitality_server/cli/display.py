"""Formatting shared by the commands: sizes and times as the person reading them expects."""

from datetime import datetime


def format_bytes(n: float) -> str:
    """A size in decimal units with one decimal: `1.5 GB`, `3.2 MB`, otherwise kB."""
    if n >= 1e9:
        return f"{n / 1e9:.1f} GB"
    if n >= 1e6:
        return f"{n / 1e6:.1f} MB"
    return f"{n / 1e3:.1f} kB"


def format_received(iso_ts: str, now: datetime) -> str:
    """A UTC ISO 8601 timestamp (`2026-10-25T21:30:37.449Z`) in this machine's local time,
    with its zone so it cannot be misread: `HH:MM:SS EDT` if that is the same local day as
    `now`, else `YYYY-MM-DD HH:MM EDT`."""
    when = datetime.fromisoformat(iso_ts).astimezone()
    if when.date() == now.astimezone().date():
        return when.strftime("%H:%M:%S %Z")
    return when.strftime("%Y-%m-%d %H:%M %Z")
