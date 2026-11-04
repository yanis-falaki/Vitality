"""One modeler session: offer a batch of evidence to Claude Code, check its work, and commit or revert it.

`run_session` is the whole harness of the modeler spec, section 5: it refuses a model folder with
foreign changes, stages the oldest waiting evidence, runs Claude Code in the model folder, checks the
result (resuming the session once with the problems), then commits or reverts, and appends one record
to `<records>/sessions.jsonl`. The session's stream is kept as `<records>/transcripts/<run>.jsonl`.
"""

import json
import os
import signal
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from vitality_modeler import repo
from vitality_modeler.claude import (
    StreamResult,
    claude_command,
    model_index,
    read_stream,
    run_message,
    session_environment,
)
from vitality_modeler.config import ModelerConfig
from vitality_usermodel.check import check
from vitality_usermodel.formats import OperationsMeta, read_file

Outcome = Literal["committed", "reverted", "failed", "limited"]

_CHANGES: tuple[str, ...] = ("actions", "activities_created", "activities_revised", "activities_merged",
            "strivings_created", "strivings_revised")
_FIX = "Fix these, then run vitality-usermodel check."
_STDERR_TAIL = 500  # characters of stderr kept in `detail`


@dataclass(frozen=True)
class SessionRecord:
    """One line of `sessions.jsonl`: the user-model spec's `runs.jsonl` fields, plus `detail`.

    Times are ISO 8601 UTC with `Z`. `check_errors` are the problems that led to the resume, or that
    remained when the session was reverted ("path: message"). `changes` are zeros unless committed.
    Tokens and cost are those of the last attempt that reported them: a resumed session's result already
    carries the earlier attempt's totals (Claude Code 2.1.309; to be confirmed on the first real run), so
    they are not summed. None when no attempt reported them.
    """

    run: str                        # "2026-11-04T12-00-00Z"
    started: str
    ended: str
    trigger: str
    operations: dict[str, Any]      # {"count", "from", "to"}: count and first and last interaction offered
    model: str
    playbook: str                   # the playbook's version (hash)
    outcome: Outcome
    attempts: int                   # 1, or 2 when the session was resumed
    check_errors: list[str]
    commit: str | None
    changes: dict[str, int]
    duration_ms: int
    input_tokens: int | None
    output_tokens: int | None
    cost: float | None
    transcript: str                 # relative to the records folder
    detail: str = ""                # why it failed; "" otherwise

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def run_session(model: Path, records: Path, config: ModelerConfig, playbook: str, playbook_version: str, *,
                trigger: str, captures: str | None = None, claude: str = "claude",
                environ: Mapping[str, str] = os.environ,
                clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> SessionRecord | None:
    """Run one session on the oldest waiting evidence of `model`; None when nothing is waiting.

    Raises RuntimeError, listing them, when the model folder has changes other than untracked evidence.
    A run that is limited, fails, times out (`config.run_timeout_seconds`, over both attempts) or still
    has check problems after one resume is reverted. On KeyboardInterrupt, or any other exception from
    staging to the commit, the session is killed and reverted, its record is appended (outcome `failed`,
    detail `interrupted` or the error), and the exception is raised again. If a revert itself fails, the
    record is still appended (outcome `failed`, detail naming both errors) and the revert's error is raised.
    """
    foreign = repo.foreign_changes(model)
    if foreign:
        raise RuntimeError(f"the model folder has changes other than new evidence: {', '.join(foreign)}")
    offered = repo.choose_batch(model, config.max_operations)
    if offered is None:
        return None
    started = clock().astimezone(UTC)
    run_id = started.strftime("%Y-%m-%dT%H-%M-%SZ")
    metas: list[OperationsMeta] = []
    for path in offered.operations:
        meta, _ = read_file(model / path, "operations")
        assert isinstance(meta, OperationsMeta)
        metas.append(meta)
    transcript = f"transcripts/{run_id}.jsonl"
    (records / "transcripts").mkdir(parents=True, exist_ok=True)
    start = repo.head(model)  # staging does not move HEAD

    tally = _Tally()

    def give_up(outcome: Outcome, detail: str) -> SessionRecord:
        # Revert and record; if the revert fails, record that too and raise its error.
        try:
            repo.revert(model, start, offered)
        except BaseException as error:
            _append(records, _record(run_id, started, clock(), trigger, offered, metas, config,
                                     playbook_version, "failed", tally, None, dict.fromkeys(_CHANGES, 0),
                                     transcript, f"{detail}; revert failed: {_describe(error)}"))
            raise
        record = _record(run_id, started, clock(), trigger, offered, metas, config, playbook_version,
                         outcome, tally, None, dict.fromkeys(_CHANGES, 0), transcript, detail)
        _append(records, record)
        return record

    commit: str | None = None
    changes = dict.fromkeys(_CHANGES, 0)
    try:
        repo.stage(model, offered)
        work = _Work(model, records / transcript, config, playbook, claude, session_environment(environ),
                     time.monotonic() + config.run_timeout_seconds, tally)
        outcome, detail = work.run(start, run_message(
            [(p, m.start) for p, m in zip(offered.operations, metas)], *model_index(model), captures))
        if outcome == "committed":
            counted = repo.change_counts(model, start)
            activities = (counted["activities_created"] + counted["activities_revised"]
                          + counted["activities_merged"])
            strivings = counted["strivings_created"] + counted["strivings_revised"]
            commit = repo.commit_all(model, f"modeler run {run_id}: {counted['actions']} actions, "
                                            f"{activities} activities, {strivings} strivings")
            changes = counted
    except BaseException as error:
        give_up("failed", _describe(error))
        raise
    if commit is None:
        return give_up(outcome, detail)
    record = _record(run_id, started, clock(), trigger, offered, metas, config, playbook_version,
                     outcome, tally, commit, changes, transcript, detail)
    _append(records, record)
    return record


def _describe(error: BaseException) -> str:
    return "interrupted" if isinstance(error, KeyboardInterrupt) else f"{type(error).__name__}: {error}"


@dataclass
class _Tally:
    # What the attempts come to. A resumed attempt's totals include the earlier attempt's, so each
    # attempt that reports a total replaces the one before.
    attempts: int = 0
    check_errors: list[str] = field(default_factory=lambda: [])
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost: float | None = None

    def add(self, result: StreamResult) -> None:
        self.attempts += 1
        self.input_tokens = _latest(self.input_tokens, result.input_tokens)
        self.output_tokens = _latest(self.output_tokens, result.output_tokens)
        self.cost = _latest(self.cost, result.cost_usd)


@dataclass
class _Work:
    model: Path
    transcript: Path
    config: ModelerConfig
    playbook: str
    claude: str
    env: dict[str, str]
    deadline: float          # time.monotonic() after which the session is given up on
    tally: _Tally

    def run(self, start: str, message: str) -> tuple[Outcome, str]:
        """Up to two attempts; the outcome and, when failed, why. Leaves committing or reverting to the caller."""
        resume: str | None = None
        while True:
            argv = claude_command(message, self.playbook, self.config, resume=resume, claude=self.claude)
            lines, code, stderr = _invoke(argv, self.model, self.env, self.transcript, self.deadline)
            result = read_stream(lines)
            self.tally.add(result)
            if code is None:
                return "failed", f"timed out after {self.config.run_timeout_seconds:g}s"
            if result.limited:
                return "limited", result.text
            if result.is_error or code != 0:
                detail = f"exit {code}: {result.text}" + (f"; stderr: {stderr}" if stderr else "")
                return "failed", detail
            problems = [f"{p.path}: {p.message}" for p in check(self.model, start)]
            if not problems:
                return "committed", ""
            self.tally.check_errors = problems
            if resume is not None or result.session_id is None:
                return "reverted", ""
            resume = result.session_id
            message = "\n".join([*problems, "", _FIX])


def _invoke(argv: list[str], model: Path, env: dict[str, str], transcript: Path,
            deadline: float) -> tuple[list[str], int | None, str]:
    # Run one Claude Code invocation, appending its stdout to `transcript` as it arrives. Returns the
    # stdout lines, the exit code (None when killed at the deadline) and the tail of stderr. The process
    # gets its own process group so that it and its children are killed together.
    lines: list[str] = []
    with open(transcript, "a", encoding="utf-8") as out, tempfile.TemporaryFile("w+", encoding="utf-8") as err:
        process = subprocess.Popen(argv, cwd=model, env=env, stdout=subprocess.PIPE, stderr=err, text=True,
                                   encoding="utf-8", errors="replace", start_new_session=True)

        def pump() -> None:
            assert process.stdout is not None
            for line in process.stdout:
                out.write(line)
                out.flush()
                lines.append(line)

        reader = threading.Thread(target=pump, daemon=True)
        reader.start()
        code: int | None
        try:
            code = process.wait(timeout=max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            _kill(process)
            code = None
        except BaseException:
            _kill(process)
            reader.join(timeout=5)
            raise
        reader.join(timeout=5)
        err.seek(0)
        stderr = err.read().strip()[-_STDERR_TAIL:]
    return list(lines), code, stderr


def _kill(process: subprocess.Popen[str]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def _record(run_id: str, started: datetime, ended: datetime, trigger: str, offered: repo.Offered,
            metas: list[OperationsMeta], config: ModelerConfig, playbook_version: str, outcome: Outcome,
            tally: _Tally, commit: str | None, changes: dict[str, int], transcript: str,
            detail: str) -> SessionRecord:
    ended = ended.astimezone(UTC)
    return SessionRecord(
        run=run_id, started=_iso(started), ended=_iso(ended), trigger=trigger,
        operations={"count": offered.count, "from": metas[0].interaction, "to": metas[-1].interaction},
        model=config.model, playbook=playbook_version, outcome=outcome, attempts=tally.attempts,
        check_errors=tally.check_errors, commit=commit, changes=changes,
        duration_ms=round((ended - started).total_seconds() * 1000),
        input_tokens=tally.input_tokens, output_tokens=tally.output_tokens, cost=tally.cost,
        transcript=transcript, detail=detail)


def _append(records: Path, record: SessionRecord) -> None:
    records.mkdir(parents=True, exist_ok=True)
    with open(records / "sessions.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(record.to_json()) + "\n")


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _latest[N: (int, float)](earlier: N | None, later: N | None) -> N | None:
    return earlier if later is None else later
