"""The check of a model folder: every rule of the design spec, section 9, reported as problems.

The check reads the files and git, and never changes anything. It reports every problem it finds
rather than stopping at the first. Each rule is one private function below `check`.
"""

import re
import subprocess
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel

from vitality_usermodel.formats import (
    ActionMeta,
    ActivityMeta,
    FormatError,
    Kind,
    OperationsMeta,
    StrivingMeta,
    kind_of,
    parse,
)
from vitality_usermodel.ids import (
    ACTION_ID,
    ACTIVITY_ID,
    INTERACTION,
    OPERATION_REF,
    STRIVING_ID,
    expected_path,
    issued,
)


@dataclass(frozen=True)
class Problem:
    """One broken rule: `path` is relative to the model folder (`"."` for the whole model)."""

    path: str
    message: str


def check(model_dir: Path, since: str | None = None) -> list[Problem]:
    """Every problem in the model at `model_dir`, sorted by path; empty if the model is valid.

    Reads every `.md` file under the five level folders. Evidence "in the index" (rule 4) is what
    `git ls-files -- operations` lists, committed or staged; if `model_dir` is not the top of a git
    repository, the index is empty. With `since` (a git revision), also checks what changed in the
    working tree since then (rules 2 and 6), untracked files included.
    Raises ValueError if `since` is given and `model_dir` is not a git repository or `since` is not
    a commit, or OSError if `model_dir` is not a folder or git cannot be run.
    """
    if not model_dir.is_dir():
        raise NotADirectoryError(f"not a folder: {model_dir}")
    repo = _is_repo(model_dir)
    changes: list[tuple[str, str]] = []
    if since is not None:
        if not repo:
            raise ValueError("not a git repository")
        verified = _git(model_dir, "rev-parse", "--verify", "--quiet", f"{since}^{{commit}}")
        if verified.returncode != 0:
            raise ValueError(f"not a commit: {since}")
        changes = _changes(model_dir, since)
    files, problems = _frontmatter(model_dir)
    problems += _ids(model_dir, files, since, changes)
    problems += _links(files, {problem.path for problem in problems})
    problems += _coverage(model_dir, files, repo)
    problems += _times(files)
    if since is not None:
        problems += _changed_files(model_dir, since, changes)
    return sorted(problems, key=lambda p: p.path)


# A parsed file: its kind and frontmatter, keyed by its path relative to the model folder.
_Files = dict[str, tuple[Kind, BaseModel]]

_LEVEL_DIRS = ("observations", "operations", "actions", "activities", "strivings")
_OP_ID = re.compile(r"op[1-9]\d*")
_ID_FORMAT: dict[Kind, tuple[re.Pattern[str], str]] = {
    "observation": (INTERACTION, "an interaction name"),
    "operations": (INTERACTION, "an interaction name"),
    "action": (ACTION_ID, "an action ID"),
    "activity": (ACTIVITY_ID, "an activity ID"),
    "striving": (STRIVING_ID, "a striving ID"),
}


def _frontmatter(model_dir: Path) -> tuple[_Files, list[Problem]]:
    # Rule 1: parse every file under the level folders; the ones that parse are returned.
    files: _Files = {}
    problems: list[Problem] = []
    for level in _LEVEL_DIRS:
        for path in sorted((model_dir / level).rglob("*.md")):
            relative = path.relative_to(model_dir).as_posix()
            kind = kind_of(relative)
            if kind is None or not path.is_file():
                continue
            try:
                meta, _ = parse(path.read_text(encoding="utf-8"), kind)
            except (FormatError, UnicodeDecodeError) as e:
                problems.append(Problem(relative, str(e)))
                continue
            except OSError as e:
                problems.append(Problem(relative, f"cannot read: {e.strerror or e}"))
                continue
            files[relative] = (kind, meta)
    return files, problems


def _ids(
    model_dir: Path, files: _Files, since: str | None, changes: list[tuple[str, str]]
) -> list[Problem]:
    # Rule 2: ID formats, IDs matching paths, duplicates, numbers above `.next-ids`, and files
    # that kept their path since `since` but changed their ID.
    problems: list[Problem] = []
    seen: dict[tuple[Kind, str], list[str]] = defaultdict(list)
    for path, (kind, meta) in files.items():
        id = _id_of(kind, meta)
        pattern, what = _ID_FORMAT[kind]
        field = "interaction" if kind in ("observation", "operations") else "id"
        if not pattern.fullmatch(id):
            problems.append(Problem(path, f"{field}: {id} is not {what}"))
        elif (expected := expected_path(kind, meta)) != path:
            problems.append(Problem(path, f"{field} {id} belongs at {expected}"))
        seen[(kind, id)].append(path)
        if isinstance(meta, OperationsMeta):
            op_ids = [op.id for op in meta.operations]
            for i, op_id in enumerate(op_ids):
                if not _OP_ID.fullmatch(op_id):
                    problems.append(
                        Problem(path, f"operations.{i}.id: {op_id} is not an operation ID")
                    )
                elif op_ids.index(op_id) != i:
                    problems.append(Problem(path, f"operations.{i}.id: duplicate {op_id}"))
    for (_, id), where in seen.items():
        for path in where:
            for other in where:
                if other != path:
                    problems.append(Problem(path, f"duplicate id {id} (also in {other})"))
    try:
        last = {kind: issued(model_dir, kind) for kind in ("activity", "striving")}
    except (ValueError, OSError) as e:
        problems.append(Problem(".next-ids", str(e)))
    else:
        for path, (kind, meta) in files.items():
            id = _id_of(kind, meta)
            if (
                kind in last
                and _ID_FORMAT[kind][0].fullmatch(id)
                and int(id.split("-")[1]) > last[kind]
            ):
                problems.append(
                    Problem(path, f"id {id} is above the last issued {kind} number ({last[kind]})")
                )
    if since is not None:
        for status, path in changes:
            if status != "M" or path not in files:
                continue
            kind, meta = files[path]
            old = _at(model_dir, since, path, kind)
            if old is not None and (old_id := _id_of(kind, old)) != _id_of(kind, meta):
                problems.append(Problem(path, f"id changed from {old_id} since {since}"))
    return problems


def _links(files: _Files, unparsed: set[str]) -> list[Problem]:
    # Rule 3: operation references resolve to an operations file and an operation in it;
    # `part_of`, `hinders` and `relations` name existing IDs of the right kind. A link to a file
    # that exists but does not parse is not reported again (rule 1 already reports the file).
    operations = _operation_ids(files)
    activities = {meta.id for _, meta in files.values() if isinstance(meta, ActivityMeta)}
    activities |= {Path(p).stem for p in unparsed if p.startswith("activities/")}
    strivings = {meta.id for _, meta in files.values() if isinstance(meta, StrivingMeta)}
    strivings |= {Path(p).stem for p in unparsed if p.startswith("strivings/")}
    problems: list[Problem] = []
    for path, (_, meta) in files.items():
        links: list[tuple[str, list[str], re.Pattern[str], set[str], str]] = []
        if isinstance(meta, ActionMeta):
            for ref in meta.operations:
                if not OPERATION_REF.fullmatch(ref):
                    problems.append(
                        Problem(path, f"operations: {ref} is not an operation reference")
                    )
                    continue
                interaction, op_id = ref.split("#")
                if f"operations/{interaction}.md" in unparsed:
                    continue
                if interaction not in operations:
                    problems.append(
                        Problem(path, f"operations: no operations file for {interaction}")
                    )
                elif op_id not in operations[interaction]:
                    problems.append(Problem(path, f"operations: no operation {ref}"))
            links.append(("part_of", meta.part_of, ACTIVITY_ID, activities, "activity"))
        elif isinstance(meta, ActivityMeta):
            links.append(("part_of", meta.part_of, STRIVING_ID, strivings, "striving"))
            links.append(("hinders", meta.hinders, STRIVING_ID, strivings, "striving"))
        elif isinstance(meta, StrivingMeta):
            for name in ("conflicts_with", "facilitates", "serves"):
                links.append(
                    (
                        f"relations.{name}",
                        getattr(meta.relations, name),
                        STRIVING_ID,
                        strivings,
                        "striving",
                    )
                )
        for field, ids, pattern, existing, kind in links:
            for id in ids:
                if not pattern.fullmatch(id):
                    article = "an" if kind == "activity" else "a"
                    problems.append(Problem(path, f"{field}: {id} is not {article} {kind} ID"))
                elif id not in existing:
                    problems.append(Problem(path, f"{field}: no {kind} {id}"))
    return problems


def _coverage(model_dir: Path, files: _Files, repo: bool) -> list[Problem]:
    # Rule 4: no operation is in two actions, and every operation of an operations file in the
    # index (committed or staged) is in one.
    cited: dict[str, list[str]] = defaultdict(list)
    for path, (_, meta) in files.items():
        if isinstance(meta, ActionMeta):
            for ref in meta.operations:
                cited[ref].append(path)
    problems: list[Problem] = []
    for ref, where in cited.items():
        for path in sorted(set(where)):
            if where.count(path) > 1:
                problems.append(Problem(path, f"operations: {ref} is listed more than once"))
            for other in sorted(set(where) - {path}):
                problems.append(Problem(path, f"operations: {ref} is also in {other}"))
    indexed = _git_paths(model_dir, "ls-files", "-z", "--", "operations") if repo else []
    for path in sorted(set(indexed)):
        entry = files.get(path)
        if entry is not None and isinstance(meta := entry[1], OperationsMeta):
            for op in meta.operations:
                if f"{meta.interaction}#{op.id}" not in cited:
                    problems.append(Problem(path, f"{op.id} belongs to no action"))
    return problems


def _times(files: _Files) -> list[Problem]:
    # Rule 5: an action lies within the earliest start and the latest end of the operations files
    # its references resolve to.
    spans: dict[str, tuple[datetime, datetime]] = {}
    for _, meta in files.values():
        if isinstance(meta, OperationsMeta):
            spans[meta.interaction] = (meta.start, meta.end)
    problems: list[Problem] = []
    for path, (_, meta) in files.items():
        if not isinstance(meta, ActionMeta):
            continue
        cited = [spans[i] for i in {ref.split("#")[0] for ref in meta.operations} if i in spans]
        if not cited:
            continue
        start = min(s for s, _ in cited)
        end = max(e for _, e in cited)
        if meta.start < start:
            problems.append(
                Problem(
                    path,
                    f"start: {meta.start.isoformat()} is before its operations start ({start.isoformat()})",
                )
            )
        if meta.end > end:
            problems.append(
                Problem(
                    path,
                    f"end: {meta.end.isoformat()} is after its operations end ({end.isoformat()})",
                )
            )
    return problems


def _changed_files(model_dir: Path, since: str, changes: list[tuple[str, str]]) -> list[Problem]:
    # Rule 6: what changed since `since` must be allowed: locked files and tracked evidence stay
    # as they were, `user-context.md` is the owner's, and the rest stays under the modeler's paths.
    problems: list[Problem] = []
    for status, path in changes:
        verb = {"A": "added", "D": "deleted"}.get(status, "changed")
        kind = kind_of(path)
        if status != "A" and kind in ("activity", "striving"):
            old = _at(model_dir, since, path, kind)
            if isinstance(old, (ActivityMeta, StrivingMeta)) and old.locked:
                problems.append(Problem(path, f"locked file {verb} since {since}"))
                continue
        if path.startswith(("observations/", "operations/")) and kind is not None:
            if status != "A":
                problems.append(Problem(path, f"evidence {verb} since {since}"))
        elif path == "user-context.md":
            problems.append(Problem(path, f"user-context.md {verb} since {since}"))
        elif not (
            path.startswith(("actions/", "activities/", "strivings/"))
            or path in ("modeler-notes.md", ".next-ids")
        ):
            problems.append(Problem(path, f"outside the allowed paths, {verb} since {since}"))
    return problems


def _id_of(kind: Kind, meta: BaseModel) -> str:
    # The identifier of a file: the interaction for evidence, the `id` for the other kinds.
    return getattr(meta, "interaction" if kind in ("observation", "operations") else "id")


def _operation_ids(files: _Files) -> dict[str, set[str]]:
    # The operation IDs of each interaction that has an operations file.
    return {
        meta.interaction: {op.id for op in meta.operations}
        for _, meta in files.values()
        if isinstance(meta, OperationsMeta)
    }


def _at(model_dir: Path, since: str, path: str, kind: Kind) -> BaseModel | None:
    # The frontmatter of `path` as it was at `since`; None if it did not exist or did not parse.
    result = _git(model_dir, "show", f"{since}:{path}")
    if result.returncode != 0:
        return None
    try:
        return parse(result.stdout, kind)[0]
    except FormatError:
        return None


def _changes(model_dir: Path, since: str) -> list[tuple[str, str]]:
    # (status, path) of every file that differs between `since` and the working tree, with
    # untracked files as added. Renames are reported as a deletion and an addition.
    tokens = _git_paths(model_dir, "diff", "--name-status", "--no-renames", "-z", since)
    changes = [(tokens[i][0], tokens[i + 1]) for i in range(0, len(tokens) - 1, 2)]
    changes += [
        ("A", path)
        for path in _git_paths(model_dir, "ls-files", "--others", "--exclude-standard", "-z")
    ]
    return changes


def _is_repo(model_dir: Path) -> bool:
    # Whether `model_dir` is the top of a git working tree (a folder inside another repo is not).
    result = _git(model_dir, "rev-parse", "--show-toplevel")
    return result.returncode == 0 and Path(result.stdout.strip()).resolve() == model_dir.resolve()


def _git_paths(model_dir: Path, *args: str) -> list[str]:
    # The NUL-separated output of a git command that must succeed; raises ValueError otherwise.
    result = _git(model_dir, *args)
    if result.returncode != 0:
        raise ValueError(f"git {args[0]}: {result.stderr.strip()}")
    return [part for part in result.stdout.split("\0") if part]


def _git(model_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    # Run git in `model_dir`, capturing its output.
    return subprocess.run(
        ["git", "-C", str(model_dir), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
