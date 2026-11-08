"""The request to Claude for one interaction, and its reply.

Pure building blocks: the instructions (`PROMPT`), the JSON schema the reply must match, the
keyword arguments of the API call for an interaction, and the parsing of the reply.
"""

import base64
import hashlib
import re
from datetime import datetime
from importlib import resources
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from vitality_protocol.layout import INTERACTION_FILE
from vitality_protocol.messages import Interaction, Shot

from vitality_observer.config import ObserverConfig
from vitality_observer.work import WorkItem, device_of

PROMPT: str = (resources.files("vitality_observer") / "prompt.md").read_text(encoding="utf-8")
"""The instructions given to Claude with every call."""

PROMPT_VERSION: str = hashlib.sha256(PROMPT.encode("utf-8")).hexdigest()[:12]
"""First 12 hex digits of the SHA-256 of `PROMPT`; changes whenever the instructions do."""

REPLY_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "transcript": {"type": "string"},
        "operations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"text": {"type": "string"}, "confidence": {"type": "integer"}},
                "required": ["text", "confidence"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["transcript", "operations"],
    "additionalProperties": False,
}
"""The JSON schema Claude's reply must match (the checks beyond it are in `Reply`)."""

MAX_TOKENS = 16000


class ReplyOperation(BaseModel):
    """One thing the person did, as Claude reported it."""

    model_config = ConfigDict(extra="forbid")

    text: str
    confidence: int = Field(ge=1, le=10)

    @field_validator("text")
    @classmethod
    def _text_not_blank(cls, value: str) -> str:
        return _not_blank(value)


class Reply(BaseModel):
    """Claude's reply: a transcript of the screen and at least one operation."""

    model_config = ConfigDict(extra="forbid")

    transcript: str
    operations: list[ReplyOperation] = Field(min_length=1)

    @field_validator("transcript")
    @classmethod
    def _transcript_not_blank(cls, value: str) -> str:
        return _not_blank(value)


def read_interaction(item: WorkItem) -> Interaction:
    """The interaction's `interaction.json`. Raises ValueError if it is missing, unreadable or invalid."""
    path = item.folder / INTERACTION_FILE
    try:
        return Interaction.model_validate_json(path.read_bytes())
    except OSError as e:
        raise ValueError(f"cannot read {path}: {e.strerror or e}") from e
    except ValidationError as e:
        raise ValueError(f"invalid {path}: {_first_problem(e)}") from e


def local_span(interaction: Interaction) -> tuple[datetime, datetime]:
    """When the interaction started and ended, in the device's own time zone (`interaction.tz`)."""
    zone = ZoneInfo(interaction.tz)
    return interaction.start.astimezone(zone), interaction.end.astimezone(zone)


def build_params(item: WorkItem, interaction: Interaction, config: ObserverConfig) -> dict:
    """The keyword arguments of `messages.create` (and the `params` of a batch request) for `item`.

    `interaction` is the item's `interaction.json`, already read (`read_interaction`).
    The user message is a text header with the local times, then for each screenshot, in order,
    a line saying which one it is (and its screen, and
    whether it is passive) and the image. Excludes `betas`, `fallbacks` and `timeout`.
    Raises ValueError, naming the file, if a listed screenshot is missing or unreadable, or its
    name does not start with digits.
    """
    start, end = local_span(interaction)
    content: list[dict] = [{
        "type": "text",
        "text": f"Interaction {item.name} on {device_of(item.name)}, "
                f"from {start.isoformat(timespec='seconds')} to {end.isoformat(timespec='seconds')} ({interaction.tz}).",
    }]
    for shot in interaction.shots:
        path = item.folder / shot.file
        try:
            data = base64.b64encode(path.read_bytes()).decode("ascii")
        except OSError as e:
            raise ValueError(f"cannot read screenshot {shot.file} of {item.name}: {e.strerror or e}") from e
        content.append({"type": "text", "text": _shot_line(shot)})
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/webp", "data": data}})
    return {
        "model": config.model,
        "max_tokens": MAX_TOKENS,
        "system": PROMPT,
        "output_config": {"format": {"type": "json_schema", "schema": REPLY_SCHEMA}, "effort": config.effort},
        "messages": [{"role": "user", "content": content}],
    }


def parse_reply(text: str) -> Reply:
    """Claude's reply text as a `Reply`. Raises ValueError, with a one-line message, if it is not valid."""
    try:
        return Reply.model_validate_json(text)
    except ValidationError as e:
        raise ValueError(f"invalid reply: {_first_problem(e)}") from e


def _shot_line(shot: Shot) -> str:
    # "0012.webp, 12 s after the start · Main · passive:"; the screen's name only when
    # the client wrote one (several screens), "passive" only for shots taken while idle.
    parts = [f"{shot.file}, {_seconds_in(shot.file)} s after the start"]
    if shot.screen_name is not None:
        parts.append(shot.screen_name)
    if shot.trigger == "passive":
        parts.append("passive")
    return " · ".join(parts) + ":"


def _seconds_in(file: str) -> int:
    # `0007.webp` and `0007-2.webp` were both captured 7 s after the start.
    match = re.match(r"\d+", file)
    if match is None:   # the protocol guarantees shot files start with digits
        raise ValueError(f"screenshot file {file} does not start with digits")
    return int(match.group())


def _not_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("must not be blank")
    return value


def _first_problem(error: ValidationError) -> str:
    first = error.errors()[0]
    field = ".".join(str(part) for part in first["loc"])
    return " ".join(f"{field}: {first['msg']}".split() if field else first["msg"].split())
