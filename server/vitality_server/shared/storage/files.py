"""Implements `InteractionStore` (see interface.py).

Interaction storage on the local filesystem: the folders are the whole record. Besides the
Protocol's methods, `FileStore` has only its constructor, `FileStore(data_dir)`. The layout it
writes is described in `vitality_server.shared.storage`.
"""

import os
import shutil
import threading
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from pydantic import ValidationError
from vitality_protocol.layout import INTERACTION_FILE, name_matches_start
from vitality_protocol.messages import Interaction

from vitality_server.shared.storage.interface import ConflictingUpload, InteractionStore, InvalidUpload

# Older servers kept an index of the folders in SQLite; `recover` deletes it.
OLD_INDEX_FILES = ("index.sqlite", "index.sqlite-wal", "index.sqlite-shm")


class FileStore(InteractionStore):
    """Thread-safe: uploads arrive on a threadpool, so `put` runs one at a time."""

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = Path(data_dir)
        self._captures = self._data_dir / "captures"
        self._tmp = self._data_dir / "tmp"
        self._tmp.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def put(self, client: str, interaction: Interaction,
            files: Mapping[str, bytes]) -> Literal["new", "existing"]:
        _validate(client, interaction, files)
        folder = self._captures / client / interaction.name
        with self._lock:  # the whole of put, so two uploads of one interaction cannot both rename
            if folder.exists():
                self._check_same(folder, interaction)
                return "existing"
            staging = self._tmp / uuid.uuid4().hex
            staging.mkdir(parents=True)
            try:
                for name, data in files.items():
                    (staging / name).write_bytes(data)
                # Without `None` fields, so a one-screen interaction is written as before.
                (staging / INTERACTION_FILE).write_text(
                    interaction.model_dump_json(indent=2, exclude_none=True))
                folder.parent.mkdir(parents=True, exist_ok=True)
                os.rename(staging, folder)
            except BaseException:
                shutil.rmtree(staging, ignore_errors=True)
                raise
            return "new"

    def disk_free_bytes(self) -> int:
        return shutil.disk_usage(self._data_dir).free

    def recover(self) -> int:
        with self._lock:
            leftovers = list(self._tmp.iterdir())
            for leftover in leftovers:
                shutil.rmtree(leftover, ignore_errors=True)
            for name in OLD_INDEX_FILES:
                (self._data_dir / name).unlink(missing_ok=True)
            return len(leftovers)

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
