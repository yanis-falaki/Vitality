"""Git operations on the model folder: starting it, choosing what a run is offered, and undoing a failed run.

Every function takes `model`, the top of the model's git repo, and raises RuntimeError with git's
message when git fails. Evidence (`observations/`, `operations/`) stays untracked until a run
stages it; a run's commit then records the evidence and the modeler's files together.
"""

import subprocess
from dataclasses import dataclass
from pathlib import Path

from vitality_usermodel.formats import OperationsMeta, read_file

COMMITTER: tuple[str, ...] = ("-c", "user.name=Vitality modeler", "-c", "user.email=modeler@localhost")
"""Git options making the modeler the author of its commits."""

_EVIDENCE = ("observations/", "operations/")
_MODELER_PATHS = ("actions", "activities", "strivings", "modeler-notes.md", ".next-ids")


@dataclass(frozen=True)
class Offered:
    """What one run is offered: repo-relative paths of operations files and their observations."""

    operations: list[str]
    observations: list[str]
    count: int          # operations inside the operations files


def init_model(model: Path, user_context: str, modeler_notes: str) -> str:
    """`git init` in `model`, write `user-context.md` and `modeler-notes.md`, commit `model: start`; returns the commit."""
    _git(model, "init", "-q")
    (model / "user-context.md").write_text(user_context, encoding="utf-8")
    (model / "modeler-notes.md").write_text(modeler_notes, encoding="utf-8")
    _git(model, "add", "-A")
    _git(model, *COMMITTER, "commit", "-q", "-m", "model: start")
    return head(model)


def foreign_changes(model: Path) -> list[str]:
    """Paths changed in the working tree other than untracked files under `observations/` or `operations/`."""
    fields = _git(model, "status", "--porcelain", "-z", "--untracked-files=all").split("\0")
    found: list[str] = []
    i = 0
    while i < len(fields) and fields[i]:
        status, path = fields[i][:2], fields[i][3:]
        i += 2 if status[0] in "RC" else 1  # a rename or copy is followed by its source
        if status == "??" and path.startswith(_EVIDENCE):
            continue
        found.append(path)
    return found


def choose_batch(model: Path, max_operations: int) -> Offered | None:
    """The oldest untracked operations files, whole, while their operations total at most `max_operations`.

    Files are ordered by their `start`. The first is always taken, even if it alone exceeds the cap.
    Each file's observation (same relative name under `observations/`) is included if present.
    Returns None when no operations file is waiting.
    """
    waiting = _git(model, "ls-files", "--others", "--exclude-standard", "-z", "--", "operations").split("\0")
    files: list[tuple[object, str, int]] = []
    for path in filter(None, waiting):
        if not path.endswith(".md"):
            continue
        meta, _ = read_file(model / path, "operations")
        assert isinstance(meta, OperationsMeta)
        files.append((meta.start, path, len(meta.operations)))
    files.sort(key=lambda f: (f[0], f[1]))  # pyright: ignore[reportOperatorIssue]  # all datetimes
    operations: list[str] = []
    count = 0
    for _, path, n in files:
        if operations and count + n > max_operations:
            break
        operations.append(path)
        count += n
    if not operations:
        return None
    observations = [o for o in ("observations/" + p.removeprefix("operations/") for p in operations)
                    if (model / o).is_file()]
    return Offered(operations, observations, count)


def stage(model: Path, offered: Offered) -> None:
    """`git add` the offered files."""
    _git(model, "add", "--", *offered.operations, *offered.observations)


def tracked(model: Path, folder: str) -> list[str]:
    """Repo-relative paths of the files under `folder` that are in the index (committed or staged)."""
    return list(filter(None, _git(model, "ls-files", "-z", "--", folder).split("\0")))


def head(model: Path) -> str:
    """The commit HEAD points to."""
    return _git(model, "rev-parse", "HEAD").strip()


def commit_all(model: Path, message: str) -> str:
    """Stage tracked changes and the modeler's own files (not other untracked evidence), commit as the modeler, and return the commit."""
    _stage_session_changes(model)
    _git(model, *COMMITTER, "commit", "-q", "-m", message)
    return head(model)


def revert(model: Path, start: str, offered: Offered) -> None:
    """Undo a run that began at commit `start` and was staged with `stage`.

    Afterwards the tracked files are as at `start`, whatever the run created is gone, and the
    offered files exist, untracked, byte-identical to when they were staged.
    """
    paths = [*offered.operations, *offered.observations]
    _git(model, "checkout", "--", *paths)  # the index still holds them as staged
    _git(model, "reset", "-q", start)
    _git(model, "checkout", start, "--", ".")
    _git(model, "clean", "-fdq", "--", ".", ":!observations", ":!operations")


def change_counts(model: Path, start: str) -> dict[str, int]:
    """What a run changed since `start`: actions, and activities and strivings created, revised or merged (deleted)."""
    _stage_session_changes(model)
    counts: dict[str, int] = dict.fromkeys(
        ("actions", "activities_created", "activities_revised", "activities_merged",
         "strivings_created", "strivings_revised"), 0)
    diff = _git(model, "diff", "--cached", "--name-status", "--no-renames", "-z", start).split("\0")
    for status, path in zip(diff[0::2], diff[1::2]):
        if path.startswith("actions/"):
            counts["actions"] += 1
        elif path.startswith("activities/"):
            counts["activities_" + {"A": "created", "D": "merged"}.get(status, "revised")] += 1
        elif path.startswith("strivings/") and status != "D":
            counts["strivings_" + ("created" if status == "A" else "revised")] += 1
    return counts


def _stage_session_changes(model: Path) -> None:
    # Tracked changes plus what a session may create; other untracked files (evidence arriving
    # meanwhile) stay out.
    _git(model, "add", "-u")
    created = [p for p in _MODELER_PATHS if (model / p).exists()]
    if created:
        _git(model, "add", "-A", "--", *created)


def _git(model: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(model), *args], capture_output=True, text=True)
    if result.returncode != 0:
        name = next(a for a in args if not a.startswith("-") and not a.startswith("user."))
        raise RuntimeError(f"git {name} failed in {model}: {result.stderr.strip()}")
    return result.stdout
