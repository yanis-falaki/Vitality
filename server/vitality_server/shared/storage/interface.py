"""The contract for storing uploaded interactions."""

from collections.abc import Mapping
from typing import Literal, Protocol

from vitality_protocol.messages import Interaction


class InvalidUpload(Exception):
    """The upload is malformed (bad name, wrong files, wrong sizes, not WebP). The route
    answers 422; `str(exc)` is the human-readable reason that goes back to the client."""


class ConflictingUpload(Exception):
    """An interaction with this name is already stored with different content. The route
    answers 409."""


class InteractionStore(Protocol):
    """Interactions received from clients, saved on disk and indexed. Thread-safe."""

    def put(self, client: str, interaction: Interaction,
            files: Mapping[str, bytes]) -> Literal["new", "existing"]:
        """Store an interaction atomically (all of it or none) under `client`.

        Returns "new" if it was stored now, "existing" if the identical interaction was
        already there (nothing changes). `files` maps each shot's file name to its bytes.
        Raises InvalidUpload (nothing is written) or ConflictingUpload (same name, different
        content); a full disk raises OSError.
        """
        ...

    def disk_free_bytes(self) -> int:
        """Free space on the disk holding the data directory."""
        ...

    def recover(self) -> int:
        """Startup only: delete leftover tmp/ folders, add complete interaction folders that
        the index lacks to the index; return how many were added."""
        ...

    def close(self) -> None: ...
