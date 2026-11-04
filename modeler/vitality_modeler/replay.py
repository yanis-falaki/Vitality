"""The lab's replay of the scheduler over a dataset (modeler spec, section 7, steps 1-3).

`replay` starts a fresh model in `<run>/model`, then copies the dataset's evidence in batch after
batch and runs one session on each, as the scheduler will on live evidence. A batch that is not
committed stays in the model, untracked, and is offered again to the next session. The replay stops
when every operations file is committed, after `MAX_FAILED_IN_A_ROW` sessions in a row without a
commit, when the usage limit is reached, or on KeyboardInterrupt. Its summary is `<run>/replay.json`;
the sessions' records are `<run>/sessions.jsonl` and `<run>/transcripts/`.
"""

import json
import os
import shutil
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from vitality_modeler import repo
from vitality_modeler.config import ModelerConfig
from vitality_modeler.playbook import PLAYBOOK, PLAYBOOK_VERSION
from vitality_modeler.session import run_session
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
    path: str           # relative to the dataset, "operations/<interaction>.md"
    interaction: str
    count: int          # operations in the file


def replay(dataset: Path, run: Path, config: ModelerConfig, *, claude: str = "claude",
           environ: Mapping[str, str] = os.environ) -> ReplaySummary:
    """Replay `dataset` into `run`, writing `<run>/replay.json`, and return the summary.

    Raises ValueError, before anything is written, when the dataset has no `user-context.md` or
    `<run>/model` already exists. KeyboardInterrupt stops the replay (`stopped: "interrupted"`) and
    is not raised again; any other error is written as the stop reason and raised again.
    """
    context = dataset / "user-context.md"
    if not context.is_file():
        raise ValueError(f"the dataset has no user-context.md: {context}")
    model = run / "model"
    if model.exists():
        raise ValueError(f"{model} already exists")
    pending = _operations_files(dataset)
    model.mkdir(parents=True)
    repo.init_model(model, context.read_text(encoding="utf-8"), MODELER_NOTES_TEMPLATE)

    records = run / "sessions.jsonl"
    earlier = _read_outcomes(records)  # a run folder is fresh, but count only this replay's sessions
    stopped: str | None = None
    without_commit = 0
    try:
        while True:
            if repo.choose_batch(model, config.max_operations) is None:
                if not pending:
                    break
                _copy_batch(dataset, model, pending, config.max_operations)
            record = run_session(model, run, config, PLAYBOOK, PLAYBOOK_VERSION, trigger="replay",
                                 claude=claude, environ=environ)
            if record is None:
                break
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
        _finish(run, model, dataset, earlier, f"{type(error).__name__}: {error}")
        raise
    return _finish(run, model, dataset, earlier, stopped)


def _operations_files(dataset: Path) -> list[_Evidence]:
    # The dataset's operations files, oldest first by `start` (then path).
    found: list[tuple[object, _Evidence]] = []
    for path in sorted((dataset / "operations").rglob("*.md")):
        meta, _ = read_file(path, "operations")
        assert isinstance(meta, OperationsMeta)
        found.append((meta.start, _Evidence(path.relative_to(dataset).as_posix(), meta.interaction,
                                            len(meta.operations))))
    found.sort(key=lambda f: (f[0], f[1].path))  # pyright: ignore[reportOperatorIssue]  # all datetimes
    return [evidence for _, evidence in found]


def _copy_batch(dataset: Path, model: Path, pending: list[_Evidence], max_operations: int) -> None:
    # Move the next whole files, up to `max_operations` operations (the first always), from `pending`
    # into the model, each with its observation when the dataset has one.
    count = 0
    while pending and not (count and count + pending[0].count > max_operations):
        evidence = pending.pop(0)
        count += evidence.count
        observation = "observations/" + evidence.path.removeprefix("operations/")
        for path in (evidence.path, observation):
            if (dataset / path).is_file():
                (model / path).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(dataset / path, model / path)


def _read_outcomes(records: Path) -> list[str]:
    if not records.is_file():
        return []
    return [json.loads(line)["outcome"] for line in records.read_text(encoding="utf-8").splitlines() if line]


def _finish(run: Path, model: Path, dataset: Path, earlier: list[str], stopped: str | None) -> ReplaySummary:
    # Build the summary from this replay's session records and what the model committed; write it.
    sessions: dict[str, int] = {}
    for outcome in _read_outcomes(run / "sessions.jsonl")[len(earlier):]:
        sessions[outcome] = sessions.get(outcome, 0) + 1
    committed = set(repo.tracked(model, "operations"))
    summary = ReplaySummary(sessions, stopped,
                            [e.interaction for e in _operations_files(dataset) if e.path not in committed])
    run.mkdir(parents=True, exist_ok=True)
    (run / "replay.json").write_text(json.dumps(summary.to_json(), indent=2) + "\n", encoding="utf-8")
    return summary
