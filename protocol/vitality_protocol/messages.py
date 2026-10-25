"""What crosses the network: the content of `interaction.json` and the server's replies.

Pydantic models, so data is validated when it arrives. All are immutable and reject
unknown fields; every datetime must carry a timezone.
"""

from datetime import datetime, timedelta
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, PlainSerializer

from vitality_protocol.layout import INTERACTION_NAME_RE, SHOT_FILE_RE


# == Contract =============================================================================

# `Timestamp` needs its serializer to exist when it is defined, so that one helper sits
# here, above it, rather than under Implementation.
def _format_timestamp(value: datetime) -> str:
    """ISO 8601 with milliseconds (truncated, not rounded); `Z` for UTC, else `+HH:MM`."""
    text = value.isoformat(timespec="milliseconds")
    if value.utcoffset() == timedelta(0):
        return text.removesuffix("+00:00") + "Z"
    return text


# A timezone-aware datetime that is written as `2026-10-25T17:00:00.449Z` in JSON.
Timestamp = Annotated[AwareDatetime, PlainSerializer(_format_timestamp, return_type=str, when_used="json")]


# Comments, not docstrings, describe the models below: pydantic copies a class docstring
# into the OpenAPI schema, and that output must not change.
class _Message(BaseModel):
    # Base of every message: immutable, and unknown fields are rejected.
    model_config = ConfigDict(extra="forbid", frozen=True)


class Shot(_Message):
    # One screenshot of an interaction.
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
    # The reply to a stored upload: `stored` says whether it was new or already there.
    ok: Literal[True] = True
    stored: Literal["new", "existing"]


class ErrorReply(_Message):
    # The reply to a refused or failed request; `error` is the human-readable reason.
    ok: Literal[False] = False
    error: str


class Health(_Message):
    # The reply to `GET /health`.
    ok: Literal[True] = True
    disk_free_bytes: int
