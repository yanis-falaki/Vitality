"""Reading the user model for the API: the model, one action, interaction summaries, one interaction's evidence.

`ModelReader(model, captures)` reads the files through `vitality_usermodel.formats` and never writes.
Three caches keep repeated requests cheap as the model grows:

- each parsed file, by its path, modification time and size: an unchanged file is parsed once;
- each interaction's screenshot count, by its captures folder's modification time;
- the assembled `Snapshot` (the model, the summaries, the indexes, and the model's JSON, plain and
  gzipped), by the (path, mtime, size) of every file it was built from and the captures folders'
  times. It is checked at most once every `fresh` seconds (a view's burst of requests shares one
  check), and rebuilt only when something changed, from the files that did.

A malformed file is skipped and reported as a `Problem`, never raised.
"""

import gzip
import hashlib
import os
import threading
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import cast

from pydantic import BaseModel

from vitality_usermodel.formats import (
    ActionMeta,
    ActivityMeta,
    FormatError,
    Kind,
    ObservationMeta,
    OperationsMeta,
    StrivingMeta,
    read_file,
)
from vitality_usermodel.ids import ACTION_ID, INTERACTION
from vitality_web import captures, schema

# A parsed file: its frontmatter and body, or the error that kept it from parsing.
_Parsed = tuple[BaseModel, str] | FormatError | OSError
# What a file or folder is known by: (mtime_ns, size), or None when it is missing.
_Key = tuple[int, int] | None

_LEVELS: list[tuple[str, Kind]] = [("strivings", "striving"), ("activities", "activity"), ("actions", "action")]


@dataclass(frozen=True)
class Json:
    """A response body, encoded once: plain, gzipped, and its ETag."""

    plain: bytes
    gzipped: bytes
    etag: str

    @staticmethod
    def of(value: BaseModel | dict[str, schema.InteractionSummary]) -> "Json":
        if isinstance(value, BaseModel):
            plain = value.model_dump_json().encode()
        else:
            plain = schema.Interactions(value).model_dump_json().encode()
        return Json(plain, gzip.compress(plain, 5), f'"{hashlib.sha1(plain).hexdigest()[:20]}"')


@dataclass(frozen=True)
class Snapshot:
    """The model as of one check of the files, with what the endpoints look up in it."""

    signature: tuple[object, ...]
    model: schema.Model
    json: Json
    actions: dict[str, schema.Action]                     # by id
    details: dict[str, tuple[list[str], str]]             # action id -> (operation references, body)
    summaries: dict[str, schema.InteractionSummary]       # every cited interaction that parses, by name
    citing: dict[str, list[str]]                          # interaction -> ids of the actions citing it


class ModelReader:
    """Reads the model folder `model` (and counts screenshots in `captures`)."""

    def __init__(self, model: Path, captures_dir: Path, fresh: float = 2.0) -> None:
        self.model = model
        self.captures = captures_dir
        self.fresh = fresh
        self._files: dict[Path, tuple[_Key, _Parsed]] = {}
        self._shots: dict[str, tuple[_Key, int]] = {}
        self._snapshot: Snapshot | None = None
        self._checked = 0.0
        self._lock = threading.Lock()

    # -- what the endpoints ask for ---------------------------------------------------------------

    def snapshot(self) -> Snapshot:
        """The current snapshot: rebuilt when a file it was built from changed (checked every `fresh` seconds)."""
        with self._lock:
            now = time.monotonic()
            if self._snapshot is not None and now - self._checked < self.fresh:
                return self._snapshot
            levels, cited = self._scan_levels()
            signature = (*levels, *self._scan_interactions(cited))
            if self._snapshot is None or self._snapshot.signature != signature:
                self._snapshot = self._build(signature, [path for path, _ in levels], cited)
            self._checked = time.monotonic()
            return self._snapshot

    def read_model(self) -> schema.Model:
        """Every striving, activity and action (without its body), and the problems."""
        return self.snapshot().model

    def read_action(self, id: str) -> schema.ActionDetail | None:
        """One action with its operation references and body; None if there is no such action."""
        if not ACTION_ID.fullmatch(id):
            return None
        snap = self.snapshot()
        found = snap.actions.get(id)
        if found is None:
            return None
        operations, body = snap.details[id]
        return schema.ActionDetail(**found.model_dump(), operations=operations, body=body)

    def interactions(self, first: date | None, last: date | None) -> dict[str, schema.InteractionSummary]:
        """The summaries of the cited interactions that started (local day) from `first` to `last`, both included."""
        lo, hi = (first.isoformat() if first else ""), (last.isoformat() if last else "9999")
        return {name: s for name, s in self.snapshot().summaries.items() if lo <= s.start[:10] <= hi}

    def read_evidence(self, interaction: str) -> schema.Evidence | None:
        """The observation, operations and screenshots of `interaction`, and the actions citing it.

        None if `interaction` is not a valid name or has no operations file that parses.
        """
        if not INTERACTION.fullmatch(interaction):
            return None
        ops = self._parsed(self.model / "operations" / f"{interaction}.md", "operations")
        if not isinstance(ops, tuple):
            return None
        meta = cast(OperationsMeta, ops[0])
        observation = None
        obs = self._parsed(self.model / "observations" / f"{interaction}.md", "observation")
        if isinstance(obs, tuple):
            o = cast(ObservationMeta, obs[0])
            observation = schema.Observation(device=o.device, start=o.start.isoformat(),
                                             end=o.end.isoformat(), body=obs[1])
        return schema.Evidence(
            interaction=interaction, start=meta.start.isoformat(), end=meta.end.isoformat(),
            observation=observation,
            operations=[schema.Operation(id=op.id, text=op.text, confidence=op.confidence) for op in meta.operations],
            screenshots=captures.screenshots(self.captures, interaction),
            actions=self.snapshot().citing.get(interaction, []))

    def operation_counts(self, relatives: list[str]) -> dict[str, int]:
        """How many operations each operations file holds (paths relative to the model); unreadable files are left out."""
        counts: dict[str, int] = {}
        for relative in relatives:
            parsed = self._parsed(self.model / relative, "operations")
            if isinstance(parsed, tuple):
                counts[relative] = len(cast(OperationsMeta, parsed[0]).operations)
        return counts

    # -- checking the files -----------------------------------------------------------------------

    def _scan_levels(self) -> tuple[list[tuple[Path, _Key]], list[str]]:
        # Every level file with its key (in path order), and the interactions the actions cite.
        levels: list[tuple[Path, _Key]] = []
        cited: set[str] = set()
        for folder, kind in _LEVELS:
            for path, key in sorted(_tree(self.model / folder)):
                levels.append((path, key))
                if kind == "action":
                    parsed = self._parsed(path, kind, key)
                    if isinstance(parsed, tuple):
                        cited.update(ref.split("#")[0] for ref in cast(ActionMeta, parsed[0]).operations)
        return levels, sorted(cited)

    def _scan_interactions(self, cited: list[str]) -> list[tuple[str, _Key, _Key]]:
        # Each cited interaction's operations file and captures folder, by key (plain strings: this runs
        # for every interaction on every check, where pathlib's overhead would dominate).
        ops, shots = f"{self.model}/operations/", f"{self.captures}/"
        return [(name, _stat(f"{ops}{name}.md"), _stat(shots + name)) for name in cited]

    # -- building a snapshot ----------------------------------------------------------------------

    def _build(self, signature: tuple[object, ...], paths: list[Path], cited: list[str]) -> Snapshot:
        problems: list[schema.Problem] = []
        found: dict[Kind, list[tuple[BaseModel, str]]] = {kind: [] for _, kind in _LEVELS}
        for path in paths:
            kind: Kind = next(k for folder, k in _LEVELS if path.is_relative_to(self.model / folder))
            parsed = self._parsed(path, kind)
            if isinstance(parsed, tuple):
                found[kind].append(parsed)
            else:
                problems.append(schema.Problem(path=self._relative(path), error=_message(parsed)))
        metas = sorted(((cast(ActionMeta, m), b) for m, b in found["action"]), key=lambda f: (f[0].start, f[0].id))
        summaries: dict[str, schema.InteractionSummary] = {}
        for name in cited:
            summary = self._summary(name, problems)
            if summary is not None:
                summaries[name] = summary
        citing: dict[str, list[str]] = {}
        for m, _ in metas:
            for name in _interactions_of(m):
                citing.setdefault(name, []).append(m.id)
        model = schema.Model(
            strivings=[_striving(m, b) for m, b in found["striving"]],
            activities=[_activity(m, b) for m, b in found["activity"]],
            actions=[_action(m) for m, _ in metas], problems=problems)
        return Snapshot(signature=signature, model=model, json=Json.of(model), actions={a.id: a for a in model.actions},
                        details={m.id: (list(m.operations), b) for m, b in metas},
                        summaries=summaries, citing=citing)

    def _summary(self, name: str, problems: list[schema.Problem]) -> schema.InteractionSummary | None:
        path = self.model / "operations" / f"{name}.md"
        parsed = self._parsed(path, "operations")
        if not isinstance(parsed, tuple):
            missing = isinstance(parsed, FileNotFoundError)
            error = "missing, but cited by an action" if missing else _message(parsed)
            problems.append(schema.Problem(path=self._relative(path), error=error))
            return None
        meta = cast(OperationsMeta, parsed[0])
        return schema.InteractionSummary(
            start=meta.start.isoformat(), end=meta.end.isoformat(), operations=len(meta.operations),
            screenshots=self._screenshots(name), first_operation=meta.operations[0].text)

    def _screenshots(self, name: str) -> int:
        # How many screenshots `name` has, counted again only when its folder changes.
        key = _stat(self.captures / name)
        cached = self._shots.get(name)
        if cached is not None and cached[0] == key and key is not None:
            return cached[1]
        count = len(captures.screenshots(self.captures, name))
        self._shots[name] = (key, count)
        return count

    def _parsed(self, path: Path, kind: Kind, key: _Key | None = None) -> _Parsed:
        # The file at `path`, parsed once per (mtime, size); `key` when the caller has just stat-ed it.
        if key is None:
            try:
                stat = os.stat(path)
            except OSError as e:
                return e
            key = (stat.st_mtime_ns, stat.st_size)
        cached = self._files.get(path)
        if cached is not None and cached[0] == key:
            return cached[1]
        try:
            result: _Parsed = read_file(path, kind)
        except (FormatError, OSError) as e:
            result = e
        self._files[path] = (key, result)
        return result

    def _relative(self, path: Path) -> str:
        return path.relative_to(self.model).as_posix()


def _tree(folder: Path) -> list[tuple[Path, _Key]]:
    # The `.md` files under `folder` with their keys (none if it is missing); links are not followed into folders.
    out: list[tuple[Path, _Key]] = []
    try:
        entries = list(os.scandir(folder))
    except OSError:
        return out
    for entry in entries:
        try:
            if entry.is_dir(follow_symlinks=False):
                out += _tree(Path(entry.path))
            elif entry.name.endswith(".md"):
                stat = entry.stat()
                out.append((Path(entry.path), (stat.st_mtime_ns, stat.st_size)))
        except OSError:
            continue
    return out


def _stat(path: Path | str) -> _Key:
    try:
        stat = os.stat(path)
    except OSError:
        return None
    return (stat.st_mtime_ns, stat.st_size)


def _interactions_of(meta: ActionMeta) -> list[str]:
    # The interactions an action cites, in the order of its operations.
    return list(dict.fromkeys(ref.split("#")[0] for ref in meta.operations))


def _message(error: Exception) -> str:
    # The error without the path prefix that read_file adds (the problem carries the path).
    text = str(error)
    if isinstance(error, FormatError) and ": " in text:
        return text.split(": ", 1)[1]
    if isinstance(error, OSError):
        return error.strerror or type(error).__name__
    return text


def _striving(meta: BaseModel, body: str) -> schema.Striving:
    m = cast(StrivingMeta, meta)
    return schema.Striving(
        id=m.id, title=m.title, confidence=m.confidence, needs=list(m.needs), orientation=m.orientation,
        aspiration_vs_obligation=m.aspiration_vs_obligation, autonomy=m.autonomy,
        relations=schema.Relations(conflicts_with=m.relations.conflicts_with,
                                   facilitates=m.relations.facilitates, serves=m.relations.serves),
        locked=m.locked, body=body)


def _activity(meta: BaseModel, body: str) -> schema.Activity:
    m = cast(ActivityMeta, meta)
    return schema.Activity(
        id=m.id, title=m.title, part_of=m.part_of, hinders=m.hinders, confidence=m.confidence,
        status=m.status, first_seen=m.first_seen.isoformat(), last_seen=m.last_seen.isoformat(),
        locked=m.locked, body=body)


def _action(m: ActionMeta) -> schema.Action:
    return schema.Action(
        id=m.id, summary=m.summary, start=m.start.isoformat(), end=m.end.isoformat(),
        interactions=_interactions_of(m), part_of=m.part_of, confidence=m.confidence)
