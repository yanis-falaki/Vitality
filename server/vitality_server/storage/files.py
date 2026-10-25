"""Implements `InteractionStore` (see interface.py).

Interaction storage on the local filesystem, indexed in SQLite. Besides the Protocol's
methods, `FileStore` has only its constructor, `FileStore(data_dir, clock=time.time)`; the
`clock` stamps `received_at` in the index. The layout it writes is described in
`vitality_server.storage`.
"""

import os
import shutil
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import ValidationError
from vitality_protocol.layout import INTERACTION_FILE, name_matches_start
from vitality_protocol.messages import Interaction

from vitality_server.storage.interface import ConflictingUpload, InteractionStore, InvalidUpload

_SCHEMA = """
CREATE TABLE IF NOT EXISTS interactions (
    client       TEXT NOT NULL,
    name         TEXT NOT NULL,
    source       TEXT NOT NULL,
    start        TEXT NOT NULL,
    end          TEXT NOT NULL,
    local_start  TEXT NOT NULL,
    tz           TEXT NOT NULL,
    received_at  TEXT NOT NULL,
    PRIMARY KEY (client, name)
);
CREATE TABLE IF NOT EXISTS shots (
    client      TEXT NOT NULL,
    interaction TEXT NOT NULL,
    file        TEXT NOT NULL,
    ts          TEXT NOT NULL,
    trigger     TEXT NOT NULL CHECK (trigger IN ('activity','manual')),
    width       INTEGER NOT NULL,
    height      INTEGER NOT NULL,
    bytes       INTEGER NOT NULL,
    PRIMARY KEY (client, interaction, file),
    FOREIGN KEY (client, interaction) REFERENCES interactions(client, name)
);
CREATE INDEX IF NOT EXISTS idx_shots_ts ON shots(ts);
"""


class FileStore(InteractionStore):
    """Thread-safe: uploads arrive on a threadpool, so `put` runs one at a time."""

    def __init__(self, data_dir: Path, clock: Callable[[], float] = time.time) -> None:
        self._data_dir = Path(data_dir)
        self._captures = self._data_dir / "captures"
        self._tmp = self._data_dir / "tmp"
        self._clock = clock
        self._tmp.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self._data_dir / "index.sqlite", check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)

    def put(self, client: str, interaction: Interaction,
            files: Mapping[str, bytes]) -> Literal["new", "existing"]:
        _validate(client, interaction, files)
        folder = self._captures / client / interaction.name
        with self._lock:  # the whole of put, so two uploads of one interaction cannot both rename
            if folder.exists():
                self._check_same(folder, interaction)
                self._index(client, interaction)  # in case a crash came before indexing
                return "existing"
            staging = self._tmp / uuid.uuid4().hex
            staging.mkdir(parents=True)
            try:
                for name, data in files.items():
                    (staging / name).write_bytes(data)
                (staging / INTERACTION_FILE).write_text(interaction.model_dump_json(indent=2))
                folder.parent.mkdir(parents=True, exist_ok=True)
                os.rename(staging, folder)
            except BaseException:
                shutil.rmtree(staging, ignore_errors=True)
                raise
            self._index(client, interaction)
            return "new"

    def disk_free_bytes(self) -> int:
        return shutil.disk_usage(self._data_dir).free

    def recover(self) -> int:
        with self._lock:
            for leftover in self._tmp.iterdir():
                shutil.rmtree(leftover, ignore_errors=True)
            indexed = 0
            for path in sorted(self._captures.glob(f"*/*/*/{INTERACTION_FILE}")):
                client = path.parts[-4]
                try:
                    interaction = Interaction.model_validate_json(path.read_text())
                except (OSError, ValueError, ValidationError):
                    continue  # unreadable: leave it for a human, do not index it
                if path.parent != self._captures / client / interaction.name:
                    continue  # the folder does not match what it says it is
                if self._index(client, interaction):
                    indexed += 1
            return indexed

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _check_same(self, folder: Path, interaction: Interaction) -> None:
        """Raise ConflictingUpload unless the stored interaction.json says the same thing."""
        try:
            stored = Interaction.model_validate_json((folder / INTERACTION_FILE).read_text())
        except (OSError, ValueError, ValidationError) as e:
            raise ConflictingUpload(
                f"{interaction.name} is already stored but its {INTERACTION_FILE} is unreadable"
            ) from e
        if stored != interaction:
            raise ConflictingUpload(
                f"{interaction.name} is already stored with different content")

    def _index(self, client: str, interaction: Interaction) -> bool:
        """Add the interaction to the index if absent; whether it was added. Caller holds the lock."""
        received_at = _format_ts(self._clock())
        i = interaction.model_dump(mode="json")
        with self._conn:
            added = self._conn.execute(
                "INSERT OR IGNORE INTO interactions "
                "(client, name, source, start, end, local_start, tz, received_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (client, i["name"], i["source"], i["start"], i["end"], i["local_start"],
                 i["tz"], received_at),
            ).rowcount == 1
            if added:
                self._conn.executemany(
                    "INSERT INTO shots (client, interaction, file, ts, trigger, width, height, "
                    "bytes) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    [(client, i["name"], s["file"], s["ts"], s["trigger"], s["width"],
                      s["height"], s["bytes"]) for s in i["shots"]],
                )
        return added


def _validate(client: str, interaction: Interaction, files: Mapping[str, bytes]) -> None:
    """Raise InvalidUpload, before anything is written, if the upload is not acceptable."""
    if not client or "/" in client or "\\" in client or "\0" in client or ".." in client:
        raise InvalidUpload(f"invalid client name: {client!r}")
    if not name_matches_start(interaction.name, interaction.start):
        raise InvalidUpload(
            f"interaction name {interaction.name!r} does not match its start {interaction.start}")
    expected = [shot.file for shot in interaction.shots]
    if len(set(expected)) != len(expected):
        raise InvalidUpload("interaction lists the same shot in its files more than once")
    if set(files) != set(expected):
        raise InvalidUpload(
            f"files sent {sorted(files)} do not match the shots listed {sorted(expected)}")
    for shot in interaction.shots:
        data = files[shot.file]
        if len(data) != shot.bytes:
            raise InvalidUpload(
                f"{shot.file}: size is {len(data)} bytes but the interaction says {shot.bytes}")
        if data[0:4] != b"RIFF" or data[8:12] != b"WEBP":
            raise InvalidUpload(f"{shot.file} is not a WebP image")


def _format_ts(epoch: float) -> str:
    """UTC ISO 8601 with milliseconds, e.g. 2026-10-25T17:00:00.449Z."""
    text = datetime.fromtimestamp(epoch, UTC).isoformat(timespec="milliseconds")
    return text.removesuffix("+00:00") + "Z"
