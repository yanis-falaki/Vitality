"""The contract for saving screenshots and looking them up."""

from dataclasses import dataclass
from typing import Literal, Protocol

from PIL import Image
from vitality_protocol.messages import Interaction


class OldLayoutError(RuntimeError):
    """The data directory still holds the layout from before interactions: a `captures`
    table in index.sqlite, or screenshots directly under captures/<date>/. The one-time
    migration has to run first; until then nothing may open the index or clean up files,
    since every old screenshot would look like an orphan. Raised by `check_layout`."""


@dataclass(frozen=True)
class ShotRow:
    """One saved screenshot, as recorded in the index."""

    interaction: str   # "2026-10-25/17-00-00"
    file: str          # "0004.webp"
    ts: str            # UTC ISO 8601 with milliseconds, e.g. 2026-10-21T14:02:05.312Z
    trigger: Literal["activity", "manual"]
    width: int
    height: int
    bytes: int

    @property
    def path(self) -> str:
        """Relative to the data directory."""
        return f"captures/{self.interaction}/{self.file}"


@dataclass(frozen=True)
class InteractionRow:
    """The start of an interaction, recorded with its first shot."""

    name: str          # "2026-10-25/17-00-00"
    start: str         # UTC ISO 8601 with milliseconds
    local_start: str   # ISO 8601 with offset and milliseconds, e.g. 2026-10-25T10:00:00.449-07:00
    tz: str            # IANA name, e.g. "America/Vancouver"
    source: str = "screen"


class CaptureIndex(Protocol):
    """The searchable record of interactions and their shots. Thread-safe."""

    def insert_shot(self, shot: ShotRow, new_interaction: InteractionRow | None = None) -> None:
        """Record a shot; with `new_interaction`, its interaction too, in one transaction."""
        ...

    def has_interaction(self, name: str) -> bool: ...

    def shot_files(self, name: str) -> set[str]:
        """The file names already recorded for an interaction."""
        ...

    def end_interaction(self, name: str) -> None:
        """Close an interaction: its `end` becomes its last shot's `ts`."""
        ...

    def open_interactions(self) -> list[str]:
        """Names of interactions that have no `end` yet."""
        ...

    def interaction(self, name: str) -> Interaction:
        """An interaction with its shots in time order (the content of interaction.json).

        One that is still open is returned as if it ended at its last shot.
        """
        ...

    def shot_paths(self) -> set[str]:
        """Every indexed file path, relative to the data directory."""
        ...

    def count_shots_since(self, ts: str) -> int:
        """How many shots have a timestamp at or after `ts`."""
        ...

    def last_shot_ts(self) -> str | None:
        """The newest shot's timestamp, or None if there are none."""
        ...

    def pending_uploads(self) -> list[str]:
        """Ended interactions not yet uploaded and not rejected; oldest first."""
        ...

    def pending_count(self) -> int: ...

    def mark_uploaded(self, name: str, ts: str) -> None: ...

    def mark_failed(self, name: str, error: str) -> None:
        """Record that the server rejected an interaction; it is no longer pending."""
        ...

    def close(self) -> None: ...


class CaptureWriter(Protocol):
    """Saves screenshots to disk, groups them into interactions and indexes them."""

    def write(self, image: Image.Image, start: float, ts: float,
              trigger: Literal["activity", "manual"]) -> ShotRow:
        """Save one screenshot crash-safely (file first, then index row); return its row.

        `start` (epoch seconds) identifies the interaction the shot belongs to; the first
        shot for a `start` opens the interaction, and records the system's time zone at
        that moment (its `tz` and `local_start`). `ts` is the capture time. Raises what
        encoding, the disk or the index raise; the index then has no row for the shot
        (a file it leaves is removed by `cleanup_orphans` at the next start).
        """
        ...

    def end(self, start: float) -> str | None:
        """Close the interaction opened for `start` and write its interaction.json.

        Returns the interaction's name, or None if it has no shots.
        """
        ...

    def close_interrupted(self) -> int:
        """Close interactions a crash left open (end + interaction.json); return how many.
        Startup only."""
        ...

    def cleanup_orphans(self) -> int:
        """Delete files left without an index row by a crash; return how many. Startup only.

        Only files at the interaction depth (captures/<date>/<time>/<file>) are considered,
        so files of the old layout or anything else in the tree are never deleted.
        """
        ...

    def disk_usage_bytes(self) -> int:
        """Total size of all saved screenshots."""
        ...
