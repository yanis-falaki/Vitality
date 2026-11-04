"""What a run records about itself: the git state of the code, its folder name and `run.yaml`."""

import re
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yaml
from vitality_usermodel.formats import write_text_atomic

LABEL_RE = re.compile(r"[a-z0-9][a-z0-9._-]*")
"""What a run's label may look like: it becomes the end of a folder name."""


@dataclass(frozen=True)
class GitState:
    """The code a run executes, as git sees it."""

    commit: str             # full hash of HEAD
    dirty: bool             # tracked files differ from HEAD
    diff: str               # `git diff HEAD`: the whole difference ("" when clean)
    untracked: list[str]    # untracked, non-ignored files under the watched folders


def git_state(repo: Path, watched: Sequence[str]) -> GitState:
    """The git state of `repo`; `untracked` only looks under the `watched` folders (relative paths).

    Raises RuntimeError, with git's message, if `repo` is not a git repository or git fails.
    """
    commit = _git(repo, "rev-parse", "HEAD").strip()
    diff = _git(repo, "diff", "HEAD")
    untracked = _git(repo, "ls-files", "--others", "--exclude-standard", "--", *watched).splitlines()
    return GitState(commit, diff != "", diff, untracked)


def run_folder_name(started: datetime, commit: str, dirty: bool, label: str | None) -> str:
    """`<YYYY-MM-DDTHH-MM-SS>-<commit7>[-dirty][-<label>]`. Raises ValueError for a label not matching `LABEL_RE`."""
    name = f"{started.strftime('%Y-%m-%dT%H-%M-%S')}-{commit[:7]}"
    if dirty:
        name += "-dirty"
    if label is not None:
        if not LABEL_RE.fullmatch(label):
            raise ValueError(f"label must be lowercase letters, digits, '.', '_' or '-', got {label!r}")
        name += f"-{label}"
    return name


def write_run_yaml(path: Path, record: dict) -> None:
    """Write `record` to `path` atomically, as block-style YAML with its keys in order."""
    write_text_atomic(path, yaml.safe_dump(record, sort_keys=False, default_flow_style=False, allow_unicode=True))


def read_run_yaml(path: Path) -> dict:
    """The record in `path`. Raises OSError if it cannot be read, yaml.YAMLError if it is not YAML."""
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else {}


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"git {args[0]} failed in {repo}: {result.stderr.strip()}")
    return result.stdout
