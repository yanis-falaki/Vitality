"""Saving screenshots: WebP files grouped into interactions, plus a SQLite index.

Contract: `CaptureWriter`, `CaptureIndex`, `ShotRow` and `InteractionRow` (interface.py).
Implementations: `Writer` (writer.py) and `Store` (index.py).

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
    ShotRow,
)
from vitality_client.daemon.storage.writer import Writer, format_ts, local_tz_name

__all__ = ["CaptureIndex", "CaptureWriter", "InteractionRow", "ShotRow", "Store", "Writer",
           "format_ts", "local_tz_name"]
