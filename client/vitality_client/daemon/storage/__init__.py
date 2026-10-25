"""Saving screenshots: WebP files grouped into interactions, plus a SQLite index.

Contract: `CaptureWriter`, `CaptureIndex`, `ShotRow`, `InteractionRow` and `OldLayoutError`
(interface.py). Implementations: `Writer` (writer.py) and `Store` (index.py).
`check_layout(data_dir)` (old_layout.py) raises `OldLayoutError` while the data directory
still has the layout from before interactions; call it before opening the index.

Layout under the data directory (names are UTC; see `vitality_protocol.layout`):

    captures/2026-10-25/17-00-00/     one interaction
      0000.webp                       the screen just before the first input
      0014.webp                       a later shot, named by whole seconds since the start
      interaction.json                written when the interaction ends; present = complete
    index.sqlite

The index has two tables: `interactions` (with upload state) and `shots`.
"""

from vitality_client.daemon.storage.index import Store
from vitality_client.daemon.storage.interface import (
    CaptureIndex,
    CaptureWriter,
    InteractionRow,
    OldLayoutError,
    ShotRow,
)
from vitality_client.daemon.storage.old_layout import check_layout
from vitality_client.daemon.storage.writer import Writer, format_ts, local_tz_name

__all__ = ["CaptureIndex", "CaptureWriter", "InteractionRow", "OldLayoutError", "ShotRow",
           "Store", "Writer", "check_layout", "format_ts", "local_tz_name"]
