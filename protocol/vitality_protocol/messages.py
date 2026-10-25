"""What crosses the network: the content of `interaction.json` and the server's replies.

Pydantic models, so data is validated when it arrives. All are immutable and reject
unknown fields; every datetime must carry a timezone.
"""

from datetime import datetime, timedelta
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, PlainSerializer

from vitality_protocol.layout import INTERACTION_NAME_RE, SHOT_FILE_RE


def _format_timestamp(value: datetime) -> str:
    """ISO 8601 with milliseconds (truncated, not rounded); `Z` for UTC, else `+HH:MM`."""
    text = value.isoformat(timespec="milliseconds")
    if value.utcoffset() == timedelta(0):
        return text.removesuffix("+00:00") + "Z"
    return text


# A timezone-aware datetime that is written as `2026-10-25T17:00:00.449Z` in JSON.
Timestamp = Annotated[AwareDatetime, PlainSerializer(_format_timestamp, return_type=str, when_used="json")]


class _Message(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Shot(_Message):
    file: Annotated[str, Field(pattern=SHOT_FILE_RE.pattern)]  # "0004.webp"
    ts: Timestamp  # exact time of the capture
    trigger: Literal["activity", "manual"]
    width: int
    height: int
    bytes: int


class Interaction(_Message):
    """The content of `interaction.json`; also the `interaction` part of an upload."""

    name: Annotated[str, Field(pattern=INTERACTION_NAME_RE.pattern)]  # "2026-10-25/17-00-00"
    source: Literal["screen"]
    start: Timestamp  # first input
    end: Timestamp  # last shot
    local_start: Timestamp  # `start` in local time, with its offset
    tz: str  # IANA name, e.g. "America/Vancouver"
    shots: Annotated[list[Shot], Field(min_length=1)]  # in time order


class UploadReply(_Message):
    ok: Literal[True] = True
    stored: Literal["new", "existing"]


class ErrorReply(_Message):
    ok: Literal[False] = False
    error: str


class Health(_Message):
    ok: Literal[True] = True
    disk_free_bytes: int
