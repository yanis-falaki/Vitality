"""One headless Claude Code call that turns a batch of interactions into observation files.

The prompt (`prompt.md`) tells Claude where and how to write; nothing here checks what it wrote.
Every call is logged as one line of `<state_dir>/calls.jsonl`.
"""

# == Contract ==

import asyncio
import json
import re
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
    detail: str                         # short reason on failure; "" on success
    record: dict                        # the line appended to calls.jsonl


class Observer(Protocol):
    async def observe(self, batch: Sequence[Interaction],
                      context: Sequence[tuple[str, str]]) -> CallResult:
        """Ask Claude to write one observation file per interaction in `batch`; never raises.

        `batch` is oldest first and from one device; `context` is `(name, text)` of that
        device's earlier observations, oldest first. `ok` means Claude finished without
        error. It does not mean the files were written well, or at all.
        """
        ...


Runner = Callable[[Sequence[str], Path, float], Awaitable[tuple[int, str, str]]]
"""Runs `argv` in a working directory for at most `timeout` seconds; returns (exit code, stdout, stderr)."""


# == Implementation ==

TIMED_OUT = -9      # exit code `run_process` reports for a process it had to kill
NOT_RUN = 127       # exit code `run_process` reports when the process could not be started


async def run_process(argv: Sequence[str], cwd: Path, timeout: float) -> tuple[int, str, str]:
    """Run `argv` in `cwd`; kill it after `timeout` seconds and return `(-9, "", "timed out")`.

    A process that cannot be started (such as a missing `claude`) returns `(127, "", reason)`.
    """
    try:
        process = await asyncio.create_subprocess_exec(
            *argv, cwd=cwd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as e:
        return NOT_RUN, "", f"could not run {argv[0]}: {e}"
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
    except TimeoutError:
        return TIMED_OUT, "", "timed out"
    finally:
        if process.returncode is None:      # timed out, or we were cancelled
            process.kill()
            await process.wait()
    return (
        process.returncode if process.returncode is not None else NOT_RUN,
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
    )


class ClaudeCode(Observer):
    """Observer that runs `claude -p` in `model_dir`; `run` and `clock` can be replaced in tests."""

    def __init__(self, config: ObserverConfig, run: Runner = run_process, clock: Callable[[], float] = time.time):
        self._config = config
        self._run = run
        self._clock = clock

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
        }
        if config.model:
            record["model"] = config.model
        _append(config.state_dir / "calls.jsonl", record)

        return CallResult(
            ok=error == "",
            usage_limit_reset=parse_reset(text) if usage_limited else None,
            usage_limited=usage_limited,
            detail=error,
            record=record,
        )


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


def _short(text: str, limit: int = 300) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + "..."


def _append(path: Path, record: dict) -> None:
    # Losing a log line must not fail a call whose files were already written.
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except OSError:
        pass
