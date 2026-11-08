"""Test helper: put interactions into a real `FileStore` so the CLI tests read real folders."""

import os
from datetime import UTC, datetime

from vitality_protocol.messages import Interaction
from vitality_server.shared.storage import FileStore

WEBP = b"RIFF\x10\x00\x00\x00WEBPVP8 " + b"\x00" * 8
"""A 20-byte file that passes the store's WebP header check."""


def store_interaction(store: FileStore, client: str, start: str, shots: int = 1,
                      received: datetime | None = None) -> None:
    """Store one interaction of `shots` WebP files, starting at `start` (`YYYY-MM-DDTHH:MM:SSZ`,
    UTC), for `client`; each shot is `len(WEBP)` bytes. With `received`, its folder's time is set
    to it (the store takes the time the upload finished from the folder)."""
    day, clock = start.rstrip("Z").split("T")
    interaction = Interaction.model_validate({
        "name": f"{day}/{clock.replace(':', '-')}", "source": "screen",
        "start": start, "end": start, "local_start": start, "tz": "UTC",
        "shots": [{"file": f"{i:04d}.webp", "ts": start, "trigger": "activity",
                   "width": 8, "height": 8, "bytes": len(WEBP)} for i in range(shots)]})
    store.put(client, interaction, {s.file: WEBP for s in interaction.shots})
    if received is not None:
        at = received.replace(tzinfo=received.tzinfo or UTC).timestamp()
        os.utime(store._captures / client / interaction.name, (at, at))  # pyright: ignore[reportPrivateUsage]


def store_with(data_dir, received: dict[str, list[tuple[str, int, datetime]]]) -> None:
    """Fill a store in `data_dir`: `received` maps a client to (start, shots, received_at)."""
    store = FileStore(data_dir)
    for client, items in received.items():
        for start, shots, received_at in items:
            store_interaction(store, client, start, shots, received_at)
