"""The contract for saving screenshots and looking them up."""

from dataclasses import dataclass
from typing import Literal, Protocol

from PIL import Image


@dataclass(frozen=True)
class CaptureRow:
    """One saved screenshot, as recorded in the index."""

    capture_id: str
    interaction_id: str
    kind: str        # "before" or "after"
    source: str      # "screen"
    ts: str          # UTC ISO 8601 with milliseconds, e.g. 2026-10-21T14:02:05.312Z
    path: str        # relative to the data directory
    width: int
    height: int
    bytes: int


class CaptureIndex(Protocol):
    """The searchable record of saved screenshots (one row each). Thread-safe."""

    def insert(self, row: CaptureRow) -> None: ...

    def paths(self) -> set[str]:
        """Every indexed file path."""
        ...

    def count_since(self, ts: str) -> int:
        """How many captures have a timestamp at or after `ts`."""
        ...

    def last_ts(self) -> str | None:
        """The newest capture's timestamp, or None if there are none."""
        ...

    def close(self) -> None: ...


class CaptureWriter(Protocol):
    """Saves screenshots to disk and indexes them."""

    def write(self, image: Image.Image, kind: Literal["before", "after"],
              interaction_id: str, ts: float) -> CaptureRow:
        """Save one screenshot crash-safely (file first, then index row); return its row.

        `ts` is the capture time in epoch seconds; it also picks the day folder.
        """
        ...

    def cleanup_orphans(self) -> int:
        """Delete files left without an index row by a crash; return how many. Startup only."""
        ...

    def disk_usage_bytes(self) -> int:
        """Total size of all saved screenshots."""
        ...
