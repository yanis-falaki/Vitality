"""Saving screenshots: WebP files plus a SQLite index row each.

`Writer(data_dir, store, quality, new_id).write(image, request)` saves one capture
crash-safely (temp file → rename → index row) and returns its `CaptureRow`.
`Store` is the index (`index.sqlite`); it is thread-safe. Layout under data_dir:
`captures/YYYY-MM-DD/<capture_id>.webp` and `index.sqlite`.
"""

from vitality_client.storage.index import CaptureRow, Store
from vitality_client.storage.writer import Writer, format_ts

__all__ = ["CaptureRow", "Store", "Writer", "format_ts"]
