"""Saving screenshots: WebP files plus a SQLite index row each.

Contract: `CaptureWriter`, `CaptureIndex` and `CaptureRow` (interface.py).
Implementations: `Writer` (writer.py) and `Store` (index.py).

Layout under the data directory: `captures/YYYY-MM-DD/<capture_id>.webp` and
`index.sqlite`.
"""

from vitality_client.daemon.storage.index import Store
from vitality_client.daemon.storage.interface import CaptureIndex, CaptureRow, CaptureWriter
from vitality_client.daemon.storage.writer import Writer, format_ts

__all__ = ["CaptureIndex", "CaptureRow", "CaptureWriter", "Store", "Writer", "format_ts"]
