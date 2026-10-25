"""What crosses the network: the content of `interaction.json` and the server's replies.

Pydantic models, so data is validated when it arrives. All are immutable and reject
unknown fields; every datetime must carry a timezone.
"""

from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from vitality_protocol.layout import INTERACTION_NAME_RE, SHOT_FILE_RE


class _Message(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Shot(_Message):
    file: Annotated[str, Field(pattern=SHOT_FILE_RE.pattern)]  # "0004.webp"
    ts: AwareDatetime  # exact time of the capture
    trigger: Literal["activity", "manual"]
    width: int
    height: int
    bytes: int


class Interaction(_Message):
    """The content of `interaction.json`; also the `interaction` part of an upload."""

    name: Annotated[str, Field(pattern=INTERACTION_NAME_RE.pattern)]  # "2026-10-25/17-00-00"
    source: Literal["screen"]
    start: AwareDatetime  # first input
    end: AwareDatetime  # last shot
    local_start: AwareDatetime  # `start` in local time, with its offset
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
