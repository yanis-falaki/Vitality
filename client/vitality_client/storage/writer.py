"""Crash-safe screenshot writer: encode to a temp file, rename, then index."""

import os
import stat
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image

from vitality_client.policy import SnapshotRequest
from vitality_client.storage.index import CaptureRow, Store

MAX_SIDE = 16383  # WebP's per-side limit


def format_ts(epoch: float) -> str:
    """UTC ISO 8601, truncated (not rounded) to milliseconds, with a Z suffix."""
    dt = datetime.fromtimestamp(epoch, UTC)
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + f".{dt.microsecond // 1000:03d}Z"


class Writer:
    def __init__(self, data_dir: Path, store: Store, quality: int,
                 new_id: Callable[[], str]) -> None:
        self._data_dir = Path(data_dir)
        self._captures = self._data_dir / "captures"
        self._store = store
        self._quality = quality
        self._new_id = new_id

    def write(self, image: Image.Image, req: SnapshotRequest) -> CaptureRow:
        """Save the image and index it. The file is complete on disk before the
        row exists, so the index never points at a missing or partial file."""
        capture_id = self._new_id()
        date = datetime.fromtimestamp(req.ts, UTC).strftime("%Y-%m-%d")
        rel = f"captures/{date}/{capture_id}.webp"
        final = self._data_dir / rel
        tmp = final.with_name(final.name + ".tmp")
        try:
            if max(image.size) > MAX_SIDE:
                image = image.copy()  # never mutate the caller's image
                image.thumbnail((MAX_SIDE, MAX_SIDE))
            final.parent.mkdir(parents=True, exist_ok=True)
            image.save(tmp, "WEBP", quality=self._quality)
            os.replace(tmp, final)
            row = CaptureRow(capture_id, req.interaction_id, req.kind, "screen",
                             format_ts(req.ts), rel, image.width, image.height,
                             final.stat().st_size)
            self._store.insert(row)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        return row

    def cleanup_orphans(self) -> int:
        """Delete stray temp files and WebP files the index does not know about."""
        if not self._captures.is_dir():
            return 0
        known = self._store.paths()
        deleted = 0
        for p in self._captures.rglob("*"):
            if not p.is_file():
                continue
            rel = p.relative_to(self._data_dir).as_posix()
            if p.suffix == ".tmp" or (p.suffix == ".webp" and rel not in known):
                p.unlink()
                deleted += 1
        return deleted

    def disk_usage_bytes(self) -> int:
        """Total size of files under captures/. Safe to call during a write: a temp
        file renamed between the directory listing and stat() is skipped."""
        if not self._captures.is_dir():
            return 0
        total = 0
        for p in self._captures.rglob("*"):
            try:
                st = p.stat()
            except FileNotFoundError:
                continue
            if stat.S_ISREG(st.st_mode):
                total += st.st_size
        return total
