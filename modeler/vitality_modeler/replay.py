"""The lab's replay of the scheduler over a dataset (modeler spec, section 7, steps 1-3).

`prepare` starts a fresh model in `<run>/model` holding the dataset's whole evidence, untracked, as
the service's model folder holds a backlog. `replay` then runs session after session on it: each
session is offered the oldest evidence not yet committed (up to the cap) and may read the rest. A
batch that is not committed stays untracked and is offered again to the next session. The replay stops
when every operations file is committed, after `MAX_FAILED_IN_A_ROW` sessions in a row without a
commit, when the usage limit is reached, or on KeyboardInterrupt. Its summary is `<run>/replay.json`;
the sessions' records are `<run>/sessions.jsonl` and `<run>/transcripts/`. Progress goes to stderr: a line
at the start, one as each session ends, and one at the end.
"""

import json
import os
import shutil
import sys
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from vitality_modeler import repo
from vitality_modeler.config import ModelerConfig
from vitality_modeler.playbook import PLAYBOOK, PLAYBOOK_VERSION
from vitality_modeler.session import SessionRecord, run_session
from vitality_usermodel.formats import OperationsMeta, read_file

MODELER_NOTES_TEMPLATE = (
    "# Modeler notes\n\n"
    "The modeler writes this file for its next run: what it was unsure of, what to look at next.\n")
"""The starting `modeler-notes.md`; the same text as `usermodel/templates/modeler-notes.md`."""

MAX_FAILED_IN_A_ROW = 3
"""Sessions in a row without a commit after which the replay stops."""


@dataclass
class ReplaySummary:
    """What `replay` writes to `replay.json`."""

    sessions: dict[str, int] = field(default_factory=lambda: {})  # outcome -> sessions with it
    stopped: str | None = None      # why the replay stopped early; None when all was committed
    not_offered: list[str] = field(default_factory=lambda: [])  # interactions never committed, oldest first

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class _Evidence:
    path: str           # relative to the folder, "operations/<interaction>.md"
    interaction: str
    count: int          # operations in the file


def prepare(dataset: Path, run: Path) -> None:
    """Start `<run>/model`: the templates, the dataset's `user-context.md` (committed) and all its evidence.

    The evidence (`observations/`, `operations/`) is copied untracked, waiting to be offered. Raises
    ValueError, before anything is written, when the dataset has no `user-context.md` or
    `<run>/model` already exists.
    """
    context = dataset / "user-context.md"
    if not context.is_file():
        raise ValueError(f"the dataset has no user-context.md: {context}")
    model = run / "model"
    if model.exists():
        raise ValueError(f"{model} already exists")
    model.mkdir(parents=True)
    repo.init_model(model, context.read_text(encoding="utf-8"), MODELER_NOTES_TEMPLATE)
    for kind in ("observations", "operations"):
        if (dataset / kind).is_dir():
            shutil.copytree(dataset / kind, model / kind)


def replay(run: Path, config: ModelerConfig, *, claude: str = "claude",
           environ: Mapping[str, str] = os.environ) -> ReplaySummary:
    """Run sessions on `<run>/model` until nothing waits, writing `<run>/replay.json`; return the summary.

    Raises ValueError, before anything is written, when `<run>/model` does not exist (see `prepare`).
    KeyboardInterrupt stops the replay (`stopped: "interrupted"`) and is not raised again; any other
    error is written as the stop reason and raised again.
    """
    model = run / "model"
    if not (model / ".git").is_dir():
        raise ValueError(f"no model in {run}; prepare it first")
    records = run / "sessions.jsonl"
    earlier = _read_outcomes(records)  # a run folder is fresh, but count only this replay's sessions
    stopped: str | None = None
    without_commit = 0
    _progress(f"{len(_operations_files(model))} operations files to offer, "
              f"batches of up to {config.max_operations} operations")
    try:
        while True:
            record = run_session(model, run, config, PLAYBOOK, PLAYBOOK_VERSION, trigger="replay",
                                 claude=claude, environ=environ)
            if record is None:
                break
            _progress(_session_line(record))
            if record.outcome == "limited":
                stopped = "usage limit reached"
                break
            without_commit = 0 if record.outcome == "committed" else without_commit + 1
            if without_commit >= MAX_FAILED_IN_A_ROW:
                stopped = f"{MAX_FAILED_IN_A_ROW} sessions in a row without a commit"
                break
    except KeyboardInterrupt:
        stopped = "interrupted"  # run_session has reverted and recorded the session it was in
    except BaseException as error:
        _finish(run, model, earlier, f"{type(error).__name__}: {error}")
        raise
    return _finish(run, model, earlier, stopped)


def _progress(line: str) -> None:
    print(f"vitality-modeler: {line}", file=sys.stderr, flush=True)


def _session_line(record: SessionRecord) -> str:
    # "session <run>: <n> operations (<first> .. <last>), <outcome>, <seconds>s, $<cost>"
    ops = record.operations
    cost = "cost unknown" if record.cost is None else f"${record.cost:.2f}"
    return (f"session {record.run}: {ops['count']} operations ({ops['from']} .. {ops['to']}), "
            f"{record.outcome}, {record.duration_ms / 1000:.0f}s, {cost}")


def _operations_files(folder: Path) -> list[_Evidence]:
    # The operations files under `folder`, oldest first by `start` (then path).
    found: list[tuple[object, _Evidence]] = []
    for path in sorted((folder / "operations").rglob("*.md")):
        meta, _ = read_file(path, "operations")
        assert isinstance(meta, OperationsMeta)
        found.append((meta.start, _Evidence(path.relative_to(folder).as_posix(), meta.interaction,
                                            len(meta.operations))))
    found.sort(key=lambda f: (f[0], f[1].path))  # pyright: ignore[reportOperatorIssue]  # all datetimes
    return [evidence for _, evidence in found]


def _read_outcomes(records: Path) -> list[str]:
    if not records.is_file():
        return []
    return [json.loads(line)["outcome"] for line in records.read_text(encoding="utf-8").splitlines() if line]


def _finish(run: Path, model: Path, earlier: list[str], stopped: str | None) -> ReplaySummary:
    # Build the summary from this replay's session records and what the model committed; write it.
    sessions: dict[str, int] = {}
    for outcome in _read_outcomes(run / "sessions.jsonl")[len(earlier):]:
        sessions[outcome] = sessions.get(outcome, 0) + 1
    committed = set(repo.tracked(model, "operations"))
    summary = ReplaySummary(sessions, stopped,
                            [e.interaction for e in _operations_files(model) if e.path not in committed])
    run.mkdir(parents=True, exist_ok=True)
    (run / "replay.json").write_text(json.dumps(summary.to_json(), indent=2) + "\n", encoding="utf-8")
    _progress("done: " + (", ".join(f"{k} {n}" for k, n in sorted(sessions.items())) or "no sessions"))
    return summary
