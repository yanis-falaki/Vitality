"""The files of the user model: one frontmatter model per level, and reading and writing them.

Every file is Markdown with a YAML frontmatter block. The models are immutable, reject unknown
fields, and require timezone-aware times. See the design spec, section 6, for the meaning of
each field.
"""

import os
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

Kind = Literal["observation", "operations", "action", "activity", "striving"]

Confidence = Annotated[int, Field(ge=1, le=10)]

Need = Literal[
    "competence",
    "autonomy",
    "relatedness",
    "status_recognition",
    "self_coherence",
    "understanding",
    "order_predictability",
    "nurturance",
    "safety_security",
    "growth",
    "stimulation",
    "purpose_meaning",
]


class FormatError(ValueError):
    """A file's content is not valid for its kind."""


class _Meta(BaseModel):
    # Base of every frontmatter model: immutable, and unknown fields are rejected.
    model_config = ConfigDict(frozen=True, extra="forbid")


class Operation(_Meta):
    # One thing the owner did within an interaction; addressed as `<interaction>#<id>`.
    id: str  # "op1"
    text: str
    confidence: Confidence


class ObservationMeta(_Meta):
    interaction: str  # "laptop/2026-11-03/14-22-05"
    device: str
    start: AwareDatetime
    end: AwareDatetime


class OperationsMeta(_Meta):
    interaction: str
    device: str
    start: AwareDatetime
    end: AwareDatetime
    operations: Annotated[list[Operation], Field(min_length=1)]


class ActionMeta(_Meta):
    id: str  # "2026-11-03/14-22-05"
    summary: str
    start: AwareDatetime
    end: AwareDatetime
    operations: list[str]  # "<interaction>#<id>"
    part_of: list[str] = []  # activity ids; none yet is allowed
    confidence: Confidence


class ActivityMeta(_Meta):
    id: str  # "act-0042"
    title: str
    part_of: list[str] = []  # striving ids
    hinders: list[str] = []  # striving ids the activity works against
    confidence: Confidence
    status: Literal["tentative", "established"]
    first_seen: AwareDatetime
    last_seen: AwareDatetime
    locked: bool = False


class Relations(_Meta):
    conflicts_with: list[str] = []  # competes for time or values
    facilitates: list[str] = []  # helps
    serves: list[str] = []  # is a means to


class StrivingMeta(_Meta):
    id: str  # "str-0003"
    title: str
    confidence: Confidence
    needs: list[Need]
    orientation: Literal["approach", "avoidance", "mixed"]
    aspiration_vs_obligation: Literal["ideal", "ought", "mixed"]
    autonomy: Literal["autonomous", "controlled", "mixed"]
    relations: Relations = Relations()
    locked: bool = False


# The frontmatter model of each kind.
META: dict[Kind, type[BaseModel]] = {
    "observation": ObservationMeta,
    "operations": OperationsMeta,
    "action": ActionMeta,
    "activity": ActivityMeta,
    "striving": StrivingMeta,
}

# The first path part under the model's root that holds each kind.
_KIND_OF_DIR: dict[str, Kind] = {
    "observations": "observation",
    "operations": "operations",
    "actions": "action",
    "activities": "activity",
    "strivings": "striving",
}


def kind_of(relative: str) -> Kind | None:
    """The kind of the file at `relative` (a path inside the model, e.g. `activities/act-0001.md`).

    Returns None if the file is not a `.md` file or is not under one of the five level directories.
    """
    parts = Path(relative).parts
    if not relative.endswith(".md") or len(parts) < 2:
        return None
    return _KIND_OF_DIR.get(parts[0])


def parse(text: str, kind: Kind) -> tuple[BaseModel, str]:
    """Split `text` into its validated frontmatter and its body (leading blank lines removed).

    The frontmatter sits between a first line `---` and the next `---` line. The body has no final
    newline, so `render(*parse(text, kind))` reproduces a rendered file.
    Raises FormatError, with a one-line message, if there is no frontmatter, it is not a YAML
    mapping, or it does not fit the kind's model (the message is the first problem, as `<field>: <msg>`).
    """
    lines = text.splitlines()
    if not lines or lines[0].rstrip() != "---":
        raise FormatError("missing frontmatter")
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].rstrip() == "---")
    except StopIteration:
        raise FormatError("missing frontmatter") from None
    try:
        data = yaml.safe_load("\n".join(lines[1:end]))
    except yaml.YAMLError as e:
        raise FormatError(f"invalid YAML: {' '.join(str(e).split())}") from e
    if not isinstance(data, dict):
        raise FormatError("frontmatter is not a mapping")
    try:
        meta = META[kind].model_validate(data)
    except ValidationError as e:
        first = e.errors()[0]
        field = ".".join(str(part) for part in first["loc"]) or "frontmatter"
        raise FormatError(f"{field}: {first['msg']}") from e
    body = "\n".join(lines[end + 1 :]).lstrip("\n")
    return meta, body


def render(meta: BaseModel, body: str = "") -> str:
    """The file text for `meta` and `body`: frontmatter, then a blank line and the body if any.

    Lists of plain values are written inline (`part_of: [act-0042]`), mappings and lists of
    mappings in block style, and times keep their offset.
    """
    front = yaml.dump(
        meta.model_dump(mode="json"),
        Dumper=_Dumper,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=None,
        width=1000,
    )
    return f"---\n{front}---\n" + (f"\n{body}\n" if body else "")


def read_file(path: Path, kind: Kind) -> tuple[BaseModel, str]:
    """Read and parse the file at `path`. Raises FormatError (message prefixed by the path), or OSError."""
    try:
        return parse(path.read_text(encoding="utf-8"), kind)
    except FormatError as e:
        raise FormatError(f"{path}: {e}") from e


def write_file(path: Path, meta: BaseModel, body: str = "") -> None:
    """Write `meta` and `body` to `path`, creating parent directories.

    The write is atomic: the text goes to `<name>.tmp-<pid>` beside the file, which then replaces it.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    tmp.write_text(render(meta, body), encoding="utf-8")
    os.replace(tmp, path)


class _Dumper(yaml.SafeDumper):
    # YAML writer for the frontmatter: mappings always in block style; a list is written inline
    # only if it holds plain values (ids, tags), not mappings or lists.
    pass


def _represent_dict(dumper: yaml.SafeDumper, data: dict[object, object]) -> yaml.MappingNode:
    return dumper.represent_mapping("tag:yaml.org,2002:map", data, flow_style=False)


def _represent_list(dumper: yaml.SafeDumper, data: list[object]) -> yaml.SequenceNode:
    plain = not any(isinstance(item, (dict, list)) for item in data)
    return dumper.represent_sequence("tag:yaml.org,2002:seq", data, flow_style=plain)


_Dumper.add_representer(dict, _represent_dict)
_Dumper.add_representer(list, _represent_list)
