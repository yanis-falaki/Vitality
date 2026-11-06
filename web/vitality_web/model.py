"""Reading the user model for the API: the whole model, and the evidence of one interaction.

`ModelReader(model, captures)` reads the files on every call, through `vitality_usermodel.formats`.
Parsed files are cached by their path, modification time and size, so an unchanged file is not parsed
twice. A malformed file is skipped and reported as a `Problem`, never raised. Nothing is written.
"""

import os
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
from vitality_usermodel.ids import INTERACTION
from vitality_web import captures, schema

# A parsed file: its frontmatter and body, or the error that kept it from parsing.
_Parsed = tuple[BaseModel, str] | FormatError | OSError


class ModelReader:
    """Reads the model folder `model` (and counts screenshots in `captures`)."""

    def __init__(self, model: Path, captures_dir: Path) -> None:
        self.model = model
        self.captures = captures_dir
        self._cache: dict[Path, tuple[tuple[int, int], _Parsed]] = {}

    def read_model(self) -> schema.Model:
        """Every striving, activity and action, the summary of each interaction an action cites, and the problems."""
        problems: list[schema.Problem] = []
        strivings = [_striving(m, b) for m, b in self._level("strivings", "striving", problems)]
        activities = [_activity(m, b) for m, b in self._level("activities", "activity", problems)]
        found = self._level("actions", "action", problems)
        found.sort(key=lambda f: (cast(ActionMeta, f[0]).start, cast(ActionMeta, f[0]).id))
        actions = [_action(m, b) for m, b in found]
        interactions: dict[str, schema.InteractionSummary] = {}
        for name in sorted({ref.split("#")[0] for a in actions for ref in a.operations}):
            summary = self._summary(name, problems)
            if summary is not None:
                interactions[name] = summary
        return schema.Model(strivings=strivings, activities=activities, actions=actions,
                            interactions=interactions, problems=problems)

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
        citing = [a.id for a in self.read_model().actions
                  if any(ref.split("#")[0] == interaction for ref in a.operations)]
        return schema.Evidence(
            interaction=interaction, start=meta.start.isoformat(), end=meta.end.isoformat(),
            observation=observation,
            operations=[schema.Operation(id=op.id, text=op.text, confidence=op.confidence) for op in meta.operations],
            screenshots=captures.screenshots(self.captures, interaction), actions=citing)

    def operation_counts(self, relatives: list[str]) -> dict[str, int]:
        """How many operations each operations file holds (paths relative to the model); unreadable files are left out."""
        counts: dict[str, int] = {}
        for relative in relatives:
            parsed = self._parsed(self.model / relative, "operations")
            if isinstance(parsed, tuple):
                counts[relative] = len(cast(OperationsMeta, parsed[0]).operations)
        return counts

    def _level(self, folder: str, kind: Kind, problems: list[schema.Problem]) -> list[tuple[BaseModel, str]]:
        # The parsed files of one level, in path order; problems for the others.
        found: list[tuple[BaseModel, str]] = []
        for path in sorted((self.model / folder).rglob("*.md")):
            parsed = self._parsed(path, kind)
            if isinstance(parsed, tuple):
                found.append(parsed)
            else:
                problems.append(schema.Problem(path=self._relative(path), error=_message(parsed)))
        return found

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
            screenshots=len(captures.screenshots(self.captures, name)), first_operation=meta.operations[0].text)

    def _parsed(self, path: Path, kind: Kind) -> _Parsed:
        # The file at `path`, parsed once per (mtime, size).
        try:
            stat = os.stat(path)
        except OSError as e:
            return e
        key = (stat.st_mtime_ns, stat.st_size)
        cached = self._cache.get(path)
        if cached is not None and cached[0] == key:
            return cached[1]
        try:
            result: _Parsed = read_file(path, kind)
        except (FormatError, OSError) as e:
            result = e
        self._cache[path] = (key, result)
        return result

    def _relative(self, path: Path) -> str:
        return path.relative_to(self.model).as_posix()


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


def _action(meta: BaseModel, body: str) -> schema.Action:
    m = cast(ActionMeta, meta)
    return schema.Action(
        id=m.id, summary=m.summary, start=m.start.isoformat(), end=m.end.isoformat(),
        operations=m.operations, part_of=m.part_of, confidence=m.confidence, body=body)
