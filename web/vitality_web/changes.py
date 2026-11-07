"""What the modeler changed in the model, commit by commit, read from the model's git history (Home's feed).

`ChangeLog(model).page(limit, before)` lists the modeler's commits (subject `modeler run <session>: ...`),
newest first, `limit` at a time, starting after the commit `before`. Each commit is compared with its
parent: the frontmatter of every striving and activity file it added, changed or deleted is parsed on
both sides (`vitality_usermodel.formats.parse`) and turned into `ChangeItem`s (created, retitled,
status, confidence, serves, unserves, removed); action files are only counted. An activity deleted while
actions moved from it to another is reported as merged into that one.

Only read-only git commands are run (`log`, `diff-tree`, `cat-file`). A commit never changes, so what
was read of one is kept by its hash. A file that does not parse is left out; a model that is not a
git repo has no changes.
"""

import subprocess
import threading
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from pydantic import BaseModel

from vitality_usermodel.formats import ActionMeta, ActivityMeta, FormatError, Kind, StrivingMeta, parse
from vitality_web import schema

PREFIX = "modeler run "
_Level = Literal["striving", "activity"]
_ItemKind = Literal["created", "retitled", "status", "confidence", "serves", "unserves", "removed"]
_FOLDERS: dict[str, Kind] = {"strivings": "striving", "activities": "activity", "actions": "action"}


@dataclass(frozen=True)
class _Commit:
    hash: str
    parent: str | None
    time: str
    session: str


class UnknownCommit(LookupError):
    """`before` names no modeler commit of the model."""


class ChangeLog:
    """The modeler's commits in the git repo at `model`."""

    def __init__(self, model: Path) -> None:
        self.model = model
        self._read: dict[str, schema.ModelerCommit] = {}
        self._lock = threading.Lock()

    def page(self, limit: int, before: str | None = None) -> schema.Changes:
        """Up to `limit` modeler commits, newest first, after `before` (a hash or a prefix of one) if given."""
        commits = self._commits()
        if before:
            at = next((i for i, c in enumerate(commits) if c.hash.startswith(before)), None)
            if at is None:
                raise UnknownCommit(before)
            commits = commits[at + 1:]
        with self._lock:
            listed = [self._changes(c) for c in commits[:limit]]
        return schema.Changes(commits=listed, more=len(commits) > limit)

    # -- the history ------------------------------------------------------------------------------

    def _commits(self) -> list[_Commit]:
        # Every modeler commit reachable from HEAD, newest first.
        out = self._git("log", "--format=%H%x1f%P%x1f%cI%x1f%s%x1e")
        if out is None:
            return []
        commits: list[_Commit] = []
        for record in out.decode("utf-8", "replace").split("\x1e"):
            fields = record.strip("\n").split("\x1f")
            if len(fields) != 4 or not fields[3].startswith(PREFIX):
                continue
            hash, parents, time, subject = fields
            session = subject[len(PREFIX):].split(":", 1)[0].strip()
            commits.append(_Commit(hash, parents.split()[0] if parents else None, time, session))
        return commits

    def _changes(self, commit: _Commit) -> schema.ModelerCommit:
        cached = self._read.get(commit.hash)
        if cached is None:
            cached = self._read[commit.hash] = self._compare(commit)
        return cached

    def _compare(self, commit: _Commit) -> schema.ModelerCommit:
        changed = self._changed_files(commit)
        levels = [(status, path) for status, path in changed if not path.startswith("actions/")]
        moved = [path for status, path in changed if path.startswith("actions/") and status == "M"]
        wanted = [path for _, path in levels] + moved
        before = self._blobs(commit.parent, wanted) if commit.parent else {}
        after = self._blobs(commit.hash, wanted)
        merged = _merges(before, after, moved)
        items: list[schema.ChangeItem] = []
        for _, path in sorted(levels, key=lambda f: (not f[1].startswith("strivings/"), f[1])):
            items += _items(before.get(path), after.get(path), merged)
        actions = [status for status, path in changed if path.startswith("actions/")]
        return schema.ModelerCommit(commit=commit.hash, time=commit.time, session=commit.session,
                                    actions_added=actions.count("A"), actions_changed=actions.count("M"), items=items)

    def _changed_files(self, commit: _Commit) -> list[tuple[str, str]]:
        # (status, path) of the level files the commit added (A), changed (M) or deleted (D).
        sides = [commit.parent, commit.hash] if commit.parent else ["--root", commit.hash]
        out = self._git("diff-tree", "-r", "-z", "--no-renames", "--name-status", *sides, "--", *_FOLDERS)
        if out is None:
            return []
        parts = out.decode("utf-8", "replace").split("\0")
        pairs = zip(parts[0::2], parts[1::2])
        return [(status[:1], path) for status, path in pairs
                if status[:1] in "AMD" and path.endswith(".md") and path.split("/", 1)[0] in _FOLDERS]

    def _blobs(self, rev: str, paths: list[str]) -> dict[str, BaseModel]:
        # The parsed frontmatter of each path at `rev` (missing or malformed files left out), in one cat-file.
        if not paths:
            return {}
        out = self._git("cat-file", "--batch", input="".join(f"{rev}:{p}\n" for p in paths).encode())
        if out is None:
            return {}
        found: dict[str, BaseModel] = {}
        at = 0
        for path in paths:
            end = out.index(b"\n", at)
            header = out[at:end].split()
            at = end + 1
            if len(header) != 3 or header[1] != b"blob":
                continue  # "<rev>:<path> missing"
            size = int(header[2])
            text = out[at:at + size].decode("utf-8", "replace")
            at += size + 1
            try:
                found[path] = parse(text, _FOLDERS[path.split("/", 1)[0]])[0]
            except FormatError:
                continue
        return found

    def _git(self, *args: str, input: bytes | None = None) -> bytes | None:
        try:
            return subprocess.run(["git", "-C", str(self.model), *args], input=input, capture_output=True,
                                  timeout=30, check=True).stdout
        except (OSError, subprocess.SubprocessError):
            return None


def _merges(before: dict[str, BaseModel], after: dict[str, BaseModel], moved: list[str]) -> dict[str, str]:
    # Activity -> the activity most of its moved actions went to, for actions whose part_of changed.
    targets: dict[str, Counter[str]] = {}
    for path in moved:
        old, new = before.get(path), after.get(path)
        if not isinstance(old, ActionMeta) or not isinstance(new, ActionMeta):
            continue
        for gone in set(old.part_of) - set(new.part_of):
            for to in set(new.part_of) - set(old.part_of):
                targets.setdefault(gone, Counter())[to] += 1
    return {gone: counts.most_common(1)[0][0] for gone, counts in targets.items()}


def _items(old: BaseModel | None, new: BaseModel | None, merged: dict[str, str]) -> list[schema.ChangeItem]:
    # What changed between the two sides of one striving or activity file.
    if new is None and old is None:
        return []
    if old is None:
        level, meta = _level(new)
        return [schema.ChangeItem(kind="created", level=level, id=meta.id, title=meta.title)]
    if new is None:
        level, meta = _level(old)
        return [schema.ChangeItem(kind="removed", level=level, id=meta.id, title=meta.title, other=merged.get(meta.id))]
    level, n = _level(new)
    _, o = _level(old)

    def item(kind: _ItemKind, before: str | None = None, after: str | None = None,
             other: str | None = None) -> schema.ChangeItem:
        return schema.ChangeItem(kind=kind, level=level, id=n.id, title=n.title, before=before, after=after, other=other)

    items: list[schema.ChangeItem] = []
    if o.title != n.title:
        items.append(item("retitled", before=o.title, after=n.title))
    if isinstance(o, ActivityMeta) and isinstance(n, ActivityMeta) and o.status != n.status:
        items.append(item("status", before=o.status, after=n.status))
    if o.confidence != n.confidence:
        items.append(item("confidence", before=str(o.confidence), after=str(n.confidence)))
    if isinstance(o, ActivityMeta) and isinstance(n, ActivityMeta):
        items += [item("serves", other=s) for s in _added(o.part_of, n.part_of)]
        items += [item("unserves", other=s) for s in _added(n.part_of, o.part_of)]
    return items


def _level(meta: BaseModel | None) -> tuple[_Level, StrivingMeta | ActivityMeta]:
    if isinstance(meta, StrivingMeta):
        return "striving", meta
    return "activity", cast(ActivityMeta, meta)


def _added(old: Iterable[str], new: Iterable[str]) -> list[str]:
    return [x for x in new if x not in set(old)]
