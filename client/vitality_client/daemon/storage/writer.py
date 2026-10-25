"""Implements `CaptureWriter` (see interface.py).

Crash-safe screenshot writer: encode to a temp file, rename, then index.

Shots are grouped into interaction folders; `end` closes one and writes interaction.json.
`Writer` has no public methods beyond the Protocol's; its constructor takes the data
directory, the index, the WebP quality and the time-zone lookup. Public helpers of this
module, not on the Protocol:

    format_ts(epoch)   the UTC ISO 8601 timestamp (milliseconds, `Z`) the index stores
    local_tz_name()    the IANA name of the system time zone
"""

import logging
import os
import stat
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from PIL import Image
from vitality_protocol.layout import INTERACTION_FILE, interaction_name, shot_filename

from vitality_client.daemon.storage.interface import (
    CaptureIndex,
    CaptureWriter,
    InteractionRow,
    ShotRow,
)

log = logging.getLogger(__name__)

MAX_SIDE = 16383  # WebP's per-side limit
LOCALTIME = Path("/etc/localtime")


def format_ts(epoch: float) -> str:
    """UTC ISO 8601, truncated (not rounded) to milliseconds, with a Z suffix."""
    dt = datetime.fromtimestamp(epoch, UTC)
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + f".{dt.microsecond // 1000:03d}Z"


def _valid_zone(key: str) -> bool:
    try:
        ZoneInfo(key)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return False
    return True


def local_tz_name() -> str:
    """The IANA name of the system time zone: $TZ if valid, else /etc/localtime, else UTC."""
    env = os.environ.get("TZ", "")
    if env and _valid_zone(env):
        return env
    _, found, key = str(LOCALTIME.resolve()).partition("zoneinfo/")
    if found and key and _valid_zone(key):
        return key
    return "UTC"


class _IndexedNames:
    """`name in taken` is true for interaction names already in the index."""

    def __init__(self, store: CaptureIndex) -> None:
        self._store = store

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and self._store.has_interaction(name)


class Writer(CaptureWriter):
    def __init__(self, data_dir: Path, store: CaptureIndex, quality: int,
                 tz: Callable[[], str] = local_tz_name) -> None:
        """`tz` gives the system's IANA time zone; it is asked when each interaction opens,
        so a zone change (travel, DST setup) applies from the next interaction on."""
        self._data_dir = Path(data_dir)
        self._captures = self._data_dir / "captures"
        self._store = store
        self._quality = quality
        self._tz = tz
        # start -> folder name, for interactions this run has recorded a shot for.
        self._open: dict[float, str] = {}

    def write(self, image: Image.Image, start: float, ts: float,
              trigger: Literal["activity", "manual"]) -> ShotRow:
        """Save the image and index it. The file is complete on disk before the
        row exists, so the index never points at a missing or partial file."""
        name = self._open.get(start)
        new: InteractionRow | None = None
        if name is None:
            name = interaction_name(datetime.fromtimestamp(start, UTC), _IndexedNames(self._store))
            tz = self._tz()
            new = InteractionRow(
                name, format_ts(start),
                datetime.fromtimestamp(start, ZoneInfo(tz)).isoformat(timespec="milliseconds"),
                tz,
            )
            taken: set[str] = set()
        else:
            taken = self._store.shot_files(name)
        file = shot_filename(max(0, int(ts - start)), taken)
        rel = f"captures/{name}/{file}"
        final = self._data_dir / rel
        tmp = final.with_name(final.name + ".tmp")
        try:
            if max(image.size) > MAX_SIDE:
                image = image.copy()  # never mutate the caller's image
                image.thumbnail((MAX_SIDE, MAX_SIDE))
            final.parent.mkdir(parents=True, exist_ok=True)
            image.save(tmp, "WEBP", quality=self._quality)
            os.replace(tmp, final)
            row = ShotRow(name, file, format_ts(ts), trigger,
                          image.width, image.height, final.stat().st_size)
            self._store.insert_shot(row, new)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        self._open[start] = name  # only once the index has the shot
        return row

    def end(self, start: float) -> str | None:
        """Close the interaction opened for `start`; None if it has no shots."""
        name = self._open.pop(start, None)
        if name is None:
            return None
        self._finish(name)
        return name

    def close_interrupted(self) -> int:
        """Close interactions a crash left open: end = last shot, write interaction.json.
        One that fails is logged and left open (retried at the next start); the rest are
        still closed."""
        closed = 0
        for name in self._store.open_interactions():
            try:
                self._finish(name)
            except Exception as exc:
                log.error("could not close interrupted interaction %s: %s",
                          name, str(exc) or type(exc).__name__)
            else:
                closed += 1
        return closed

    def _finish(self, name: str) -> None:
        """Write interaction.json, and only then commit the end. If this fails or the
        process dies part-way, the interaction stays open and `close_interrupted` redoes it."""
        final = self._captures / name / INTERACTION_FILE
        tmp = final.with_name(final.name + ".tmp")
        try:
            final.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(self._store.interaction(name).model_dump_json(indent=2))
            os.replace(tmp, final)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        self._store.end_interaction(name)

    def cleanup_orphans(self) -> int:
        """Delete stray temp files and WebP files the index does not know about, then the
        interaction and date folders that are left empty. Only files at the interaction
        depth, captures/<date>/<time>/<file>, are considered; anything elsewhere is kept."""
        if not self._captures.is_dir():
            return 0
        known = self._store.shot_paths()
        deleted = 0
        for p in self._captures.glob("*/*/*"):
            if not p.is_file():
                continue
            rel = p.relative_to(self._data_dir).as_posix()
            if p.suffix == ".tmp" or (p.suffix == ".webp" and rel not in known):
                p.unlink()
                deleted += 1
        for d in [*self._captures.glob("*/*"), *self._captures.glob("*")]:  # times, then dates
            if d.is_dir() and not any(d.iterdir()):
                d.rmdir()
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
