"""One headless Claude Code call that turns a batch of interactions into observation files.

The prompt (`prompt.md`) tells Claude where and how to write; nothing here checks what it wrote.
Every call is logged as one line of `<state_dir>/calls.jsonl`; if that file cannot be written,
a warning is logged instead and the call goes on.
"""

# == Contract ==

import asyncio
import json
import logging
import os
import re
import signal
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Protocol

from vitality_observer.config import ObserverConfig
from vitality_observer.work import Interaction


@dataclass(frozen=True)
class CallResult:
    ok: bool
    usage_limit_reset: float | None     # epoch seconds, when the failure was a usage limit that said when it resets
    usage_limited: bool                 # the failure was a usage limit
    reached_model: bool                 # Claude got as far as working on the batch: the call timed out, or used tokens (cached ones too) or cost
    detail: str                         # short reason on failure; "" on success
    record: dict                        # the line appended to calls.jsonl


class Observer(Protocol):
    async def observe(self, batch: Sequence[Interaction],
                      context: Sequence[tuple[str, str]]) -> CallResult:
        """Ask Claude to write one observation file per interaction in `batch`; never raises.

        `batch` is oldest first and from one device; `context` is `(name, text)` of that
        device's earlier observations, oldest first. `ok` means Claude finished without
        error. It does not mean the files were written well, or at all. `reached_model` tells a
        failure that is Claude's (or the batch's) from one that is the setup's: a missing
        binary, a logged-out account or a network failure never reaches the model.
        """
        ...


Runner = Callable[[Sequence[str], Path, float], Awaitable[tuple[int, str, str]]]
"""Runs `argv` in a working directory for at most `timeout` seconds; returns (exit code, stdout, stderr)."""


# == Implementation ==

log = logging.getLogger("vitality_observer")

TIMED_OUT = -9      # exit code `run_process` reports for a process it had to kill
NOT_RUN = 127       # exit code `run_process` reports when the process could not be started


async def run_process(argv: Sequence[str], cwd: Path, timeout: float) -> tuple[int, str, str]:
    """Run `argv` in `cwd`; kill it after `timeout` seconds and return `(-9, "", "timed out")`.

    The process runs in a session of its own. When it times out, or the caller is cancelled,
    the whole session's process group is killed: the process and every command it started
    that is still running. A process that cannot be started (such as a missing `claude`)
    returns `(127, "", reason)`.
    """
    try:
        process = await asyncio.create_subprocess_exec(
            *argv, cwd=cwd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
    except OSError as e:
        return NOT_RUN, "", f"could not run {argv[0]}: {e}"
    finished = False
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
        finished = True
    except TimeoutError:
        return TIMED_OUT, "", "timed out"
    finally:
        if not finished:        # timed out, or we were cancelled
            _kill_group(process.pid)
            await process.wait()
    return (
        process.returncode if process.returncode is not None else NOT_RUN,
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
    )


def _kill_group(pid: int) -> None:
    """SIGKILL the process group led by `pid` (its own session); nothing left in it is fine."""
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


class ClaudeCode(Observer):
    """Observer that runs `claude -p` in `model_dir`; `run` and `clock` can be replaced in tests."""

    def __init__(self, config: ObserverConfig, run: Runner = run_process, clock: Callable[[], float] = time.time):
        self._config = config
        self._run = run
        self._clock = clock
        self._calls_log_broken = False      # warned that calls.jsonl cannot be written

    async def observe(self, batch: Sequence[Interaction],
                      context: Sequence[tuple[str, str]]) -> CallResult:
        config = self._config
        started = self._clock()
        try:
            argv = build_argv(build_message(batch, context, config), config)
            code, stdout, stderr = await self._run(argv, config.model_dir, config.call_timeout_seconds)
        except Exception as e:
            code, stdout, stderr = NOT_RUN, "", f"{type(e).__name__}: {e}"
        elapsed_ms = round((self._clock() - started) * 1000)

        output = _parse_output(stdout)
        error = _failure(code, output, stdout, stderr, config.call_timeout_seconds)
        text = " ".join(part for part in (_text(output, "result"), stderr, "" if output else stdout) if part)
        # A successful call's result is free text that may mention a usage limit; only a failure can be one.
        usage_limited = error != "" and "usage limit" in text.lower()

        usage = output.get("usage")
        usage = usage if isinstance(usage, dict) else {}
        record = {
            "time": datetime.fromtimestamp(started, UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "interactions": [i.name for i in batch],
            "exit": code,
            "ok": error == "",
            "detail": error,
            "duration_ms": _duration_ms(output, elapsed_ms),
            "cost": output.get("total_cost_usd"),
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "cache_read_input_tokens": usage.get("cache_read_input_tokens"),
            "cache_creation_input_tokens": usage.get("cache_creation_input_tokens"),
        }
        if config.model:
            record["model"] = config.model
        self._log_call(config.state_dir / "calls.jsonl", record)

        return CallResult(
            ok=error == "",
            reached_model=code == TIMED_OUT or _positive(
                output.get("total_cost_usd"), usage.get("input_tokens"), usage.get("output_tokens"),
                usage.get("cache_read_input_tokens"), usage.get("cache_creation_input_tokens")),
            usage_limit_reset=parse_reset(text) if usage_limited else None,
            usage_limited=usage_limited,
            detail=error,
            record=record,
        )

    def _log_call(self, path: Path, record: dict) -> None:
        """Append `record` to `path`; a failure is only logged, once until a write works again."""
        error = _append(path, record)
        if error is not None and not self._calls_log_broken:
            log.warning("cannot add to %s (%s); calls go unrecorded until it works again", path, error)
        self._calls_log_broken = error is not None


def build_message(batch: Sequence[Interaction], context: Sequence[tuple[str, str]],
                  config: ObserverConfig) -> str:
    """The prompt for one call: the batch with folder and observation paths, then the context.

    Observation paths are relative to the working directory, `config.model_dir`.
    """
    lines = ["Write an observation for each of these interactions, oldest first.", ""]
    for number, i in enumerate(batch, 1):
        lines += [
            f"{number}. {i.name}",
            f"   folder: {i.folder.resolve()}",
            f"   write to: {i.observation.relative_to(config.model_dir).as_posix()}",
        ]
    lines.append("")
    if not context:
        lines.append("Previous observations: none.")
    else:
        lines.append("Previous observations from the same device, oldest first:")
        for name, text in context:
            lines += ["", f"--- {name} ---", text.strip()]
    return "\n".join(lines)


def build_argv(message: str, config: ObserverConfig) -> list[str]:
    """The `claude` command line for one call; `prompt.md` is passed as its text."""
    prompt = resources.files("vitality_observer").joinpath("prompt.md").read_text(encoding="utf-8")
    argv = [
        "claude", "-p", message,
        "--append-system-prompt", prompt,
        "--permission-mode", "bypassPermissions",
        "--output-format", "json",
    ]
    if config.model:
        argv += ["--model", config.model]
    return argv


def parse_reset(text: str) -> float | None:
    """The reset time, as epoch seconds, from a usage-limit message like `...limit reached|1793050000`."""
    found = re.search(r"\|(\d{10})\b", text)
    return float(found.group(1)) if found else None


def _parse_output(stdout: str) -> dict:
    """The JSON object `claude --output-format json` printed; empty if stdout is anything else."""
    try:
        output = json.loads(stdout)
    except ValueError:
        return {}
    return output if isinstance(output, dict) else {}


def _text(output: dict, key: str) -> str:
    value = output.get(key)
    return value if isinstance(value, str) else ""


def _duration_ms(output: dict, measured: int) -> int | float:
    """The call's own `duration_ms` if it is a number, else the time we measured."""
    duration = output.get("duration_ms")
    if isinstance(duration, (int, float)) and not isinstance(duration, bool):
        return duration
    return measured


def _failure(code: int, output: dict, stdout: str, stderr: str, timeout: float) -> str:
    """Why the call failed in a few words, or "" if it did not."""
    if code == TIMED_OUT:
        return f"timed out after {timeout:g}s"
    if not output:
        return f"exit {code}, no JSON result: {_short(stderr or stdout)}"
    if output.get("is_error"):
        return _short(_text(output, "result") or f"exit {code}, Claude reported an error")
    if code != 0:
        return f"exit {code}: {_short(stderr)}"
    return ""


def _positive(*numbers: object) -> bool:
    """Whether any of `numbers` is a number above zero (anything else counts as zero)."""
    return any(isinstance(n, (int, float)) and not isinstance(n, bool) and n > 0 for n in numbers)


def _short(text: str, limit: int = 300) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + "..."


def _append(path: Path, record: dict) -> OSError | None:
    """Append `record` as one JSON line; return the error instead of raising it.

    Losing a log line must not fail a call whose files were already written.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except OSError as e:
        return e
    return None
