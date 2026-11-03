"""IDs of the user model: their formats, the path each file must have, and allocating new ones.

Observations and operations are named by their interaction (`<device>/<date>/<time>`), actions by
the same without the device. Activities and strivings get numbered IDs, issued from the counters
in `<model_dir>/.next-ids`.
"""

import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from vitality_usermodel.formats import Kind, write_text_atomic

# Patterns are matched with `fullmatch`. A suffix `-2`, `-3`, ... tells apart interactions that
# start in the same second; the first one has none, so `-1` is never valid.
_TIME = r"\d{4}-\d{2}-\d{2}/\d{2}-\d{2}-\d{2}(?:-[2-9]|-[1-9]\d+)?"
INTERACTION = re.compile(rf"[A-Za-z0-9][A-Za-z0-9._-]*/{_TIME}")
ACTION_ID = re.compile(_TIME)
OPERATION_REF = re.compile(INTERACTION.pattern + r"#op[1-9]\d*")
ACTIVITY_ID = re.compile(r"act-\d{4,}")
STRIVING_ID = re.compile(r"str-\d{4,}")

NumberedKind = Literal["activity", "striving"]

_PREFIX: dict[NumberedKind, str] = {"activity": "act", "striving": "str"}
_COUNTERS_FILE = ".next-ids"


def expected_path(kind: Kind, meta: BaseModel) -> str:
    """The path, relative to the model directory, that a file of `kind` with this `meta` must have.

    `meta` is the kind's frontmatter model: its `interaction` is used for observations and
    operations, its `id` for the other kinds. Returns a POSIX path such as
    `activities/act-0042.md`. Raises AttributeError if `meta` lacks that field.
    """
    if kind == "observation":
        return f"observations/{getattr(meta, 'interaction')}.md"
    if kind == "operations":
        return f"operations/{getattr(meta, 'interaction')}.md"
    directory = {"action": "actions", "activity": "activities", "striving": "strivings"}[kind]
    return f"{directory}/{getattr(meta, 'id')}.md"


def issued(model_dir: Path, kind: NumberedKind) -> int:
    """The number of the last ID issued for `kind` (0 if none), read from `<model_dir>/.next-ids`.

    Raises ValueError if the file exists but is not a JSON object of integers, or OSError if it
    cannot be read.
    """
    path = model_dir / _COUNTERS_FILE
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return 0
    return _parse_counters(text, path).get(kind, 0)


def next_id(model_dir: Path, kind: NumberedKind) -> str:
    """Issue the next ID for `kind`: `act-0001`, `act-0002`, ... or `str-0001`, ...

    Records the new number in `<model_dir>/.next-ids` (atomically, creating it and `model_dir` if
    needed) and returns the ID; numbers past 9999 get more digits. Not safe against concurrent
    callers. Raises ValueError if the existing file is malformed, or OSError on I/O failure.
    """
    path = model_dir / _COUNTERS_FILE
    try:
        counters = _parse_counters(path.read_text(encoding="utf-8"), path)
    except FileNotFoundError:
        counters = {}
    number = counters.get(kind, 0) + 1
    counters[kind] = number
    write_text_atomic(path, json.dumps(counters) + "\n")
    return f"{_PREFIX[kind]}-{number:04d}"


def _parse_counters(text: str, path: Path) -> dict[str, int]:
    # The counters file as a dict; any other content is an error naming the file.
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"{path}: invalid JSON: {e}") from e
    if not isinstance(data, dict) or not all(
        isinstance(v, int) and not isinstance(v, bool) for v in data.values()
    ):
        raise ValueError(f"{path}: not a JSON object of integers")
    return data
