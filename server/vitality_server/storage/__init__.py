"""Storing interactions uploaded by clients: WebP files in folders, plus a SQLite index.

Contract: `InteractionStore`, `InvalidUpload` and `ConflictingUpload` (interface.py).
Implementation: `FileStore` (files.py).

Layout under the data directory (names are UTC; see `vitality_protocol.layout`):

    captures/laptop/2026-10-25/17-00-00/    one interaction, under its client's name
      0000.webp
      interaction.json                      always present: a folder appears only when complete
    tmp/<uuid>/                             an upload being written; renamed into captures/
    index.sqlite

The index has two tables, `interactions` and `shots`, both keyed by client as well.
"""

from vitality_server.storage.files import FileStore
from vitality_server.storage.interface import ConflictingUpload, InteractionStore, InvalidUpload

__all__ = ["ConflictingUpload", "FileStore", "InteractionStore", "InvalidUpload"]
