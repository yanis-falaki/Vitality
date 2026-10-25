"""How an interaction is laid out on disk, on the client and on the server.

    2026-10-25/17-00-00/      one interaction (the server adds a <client>/ level above)
      0000.webp               the screen just before the first input
      0014.webp               a later shot, named by whole seconds since the start
      0014-2.webp             a second shot in the same second (e.g. a manual capture)
      interaction.json        written when the interaction ends; present = complete

Names are UTC. A name or file name that is already taken gets `-2`, `-3`, ... appended.
"""

import re
from collections.abc import Container
from datetime import UTC, datetime

INTERACTION_FILE = "interaction.json"

INTERACTION_NAME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}/\d{2}-\d{2}-\d{2}(-[1-9]\d*)?$")
SHOT_FILE_RE = re.compile(r"^\d{4,}(-[1-9]\d*)?\.webp$")

_NAME_FORMAT = "%Y-%m-%d/%H-%M-%S"


def interaction_name(start: datetime, taken: Container[str] = ()) -> str:
    """The folder name for an interaction that began at `start` (timezone-aware)."""
    if start.tzinfo is None:
        raise ValueError("interaction start must be timezone-aware")
    base = start.astimezone(UTC).strftime(_NAME_FORMAT)
    return _first_free(base, "", taken)


def name_matches_start(name: str, start: datetime) -> bool:
    """Whether `name` is the name `interaction_name` gives `start`, with or without a suffix."""
    # Names have a fixed-width base, so the base is a prefix and any rest is a `-<n>` suffix.
    return INTERACTION_NAME_RE.fullmatch(name) is not None and name.startswith(
        interaction_name(start)
    )


def shot_filename(offset_s: int, taken: Container[str] = ()) -> str:
    """The file name for a shot taken `offset_s` whole seconds after the interaction began."""
    if offset_s < 0:
        raise ValueError("shot offset must not be negative")
    return _first_free(f"{offset_s:04d}", ".webp", taken)


def _first_free(base: str, extension: str, taken: Container[str]) -> str:
    candidate, n = base + extension, 1
    while candidate in taken:
        n += 1
        candidate = f"{base}-{n}{extension}"
    return candidate
