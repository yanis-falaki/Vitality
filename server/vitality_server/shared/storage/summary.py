"""A read-only look at the stored folders: what each device has uploaded.

A plain function module for the command line (`vitality-server status`), which must work
whether or not the server is running and must never change the data folder.
"""

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from vitality_protocol.layout import INTERACTION_FILE


@dataclass(frozen=True)
class DeviceSummary:
    """What one device (client) has uploaded."""

    client: str
    interactions: int
    shots: int
    bytes: int                      # the sum of the shots' file sizes
    last_received: str | None       # when the newest upload finished (UTC ISO 8601), None if none


def read_summary(data_dir: Path) -> list[DeviceSummary]:
    """One `DeviceSummary` per client with stored interactions under `data_dir/captures/`,
    sorted by client name.

    An interaction is a `captures/<client>/<day>/<time>/` folder with an `interaction.json`;
    its shots are the `.webp` files in it. When it was received is the folder's modification
    time, which the store's rename sets when the upload finishes. Opens folders only to list
    them and never creates or changes a file.

    Raises FileNotFoundError if `data_dir` is missing and PermissionError if a folder cannot be
    listed (the server's folder is private to the `vitality` group). No `captures/` folder
    means nothing is stored yet.
    """
    os.listdir(data_dir)  # raises FileNotFoundError or PermissionError for the data folder itself
    captures = Path(data_dir) / "captures"
    if not captures.is_dir():
        return []
    summaries = []
    for client in sorted(p for p in captures.iterdir() if p.is_dir()):
        interactions = shots = size = 0
        newest: float | None = None
        for folder in client.glob("*/*/"):
            if not (folder / INTERACTION_FILE).is_file():
                continue  # not a complete upload
            interactions += 1
            newest = max(newest or 0.0, folder.stat().st_mtime)
            for entry in os.scandir(folder):
                if entry.name.endswith(".webp") and entry.is_file():
                    shots += 1
                    size += entry.stat().st_size
        if interactions:
            summaries.append(DeviceSummary(client.name, interactions, shots, size, _iso(newest)))
    return summaries


def _iso(epoch: float | None) -> str | None:
    """UTC ISO 8601 with milliseconds, e.g. 2026-10-25T17:00:00.449Z."""
    if epoch is None:
        return None
    return datetime.fromtimestamp(epoch, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
