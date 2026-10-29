import asyncio
import json
import logging
import os
import sys
from collections.abc import Sequence
from importlib import resources
from pathlib import Path

import pytest

from vitality_observer.claude import (
    ClaudeCode,
    build_argv,
    build_message,
    parse_reset,
    run_process,
)
from vitality_observer.config import ObserverConfig
from vitality_observer.work import Interaction


@pytest.fixture
def cfg(tmp_path: Path) -> ObserverConfig:
    return ObserverConfig(
        captures_dir=tmp_path / "captures",
        model_dir=tmp_path / "model",
        state_dir=tmp_path / "state",
    )


def interaction(cfg: ObserverConfig, name: str) -> Interaction:
    return Interaction(
        name, cfg.captures_dir / name, cfg.model_dir / "observations" / f"{name}.md"
    )


class FakeRunner:
    """A Runner that returns a fixed result and remembers how it was called."""

    def __init__(self, code: int = 0, stdout: str = "", stderr: str = ""):
        self.result = (code, stdout, stderr)
        self.calls: list[tuple[Sequence[str], Path, float]] = []

    async def __call__(self, argv: Sequence[str], cwd: Path, timeout: float):
        self.calls.append((argv, cwd, timeout))
        return self.result


def success_json(**overrides: object) -> str:
    data = {
        "is_error": False,
        "result": "done",
        "duration_ms": 1200,
        "total_cost_usd": 0.02,
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }
    return json.dumps(data | overrides)


def logged(cfg: ObserverConfig) -> list[dict]:
    text = (cfg.state_dir / "calls.jsonl").read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines()]


def test_message_lists_batch_then_context(cfg):
    batch = [interaction(cfg, "laptop/2026-10-25/17-00-00"), interaction(cfg, "laptop/2026-10-25/17-05-00")]
    context = [("laptop/2026-10-25/16-00-00", "Opened the mail client."), ("laptop/2026-10-25/16-30-00", "Wrote a reply.")]

    message = build_message(batch, context, cfg)

    for i in batch:
        assert str(i.folder) in message
        assert f"observations/{i.name}.md" in message
    assert str(cfg.model_dir) not in message        # observation paths are relative to the working directory
    assert message.index(batch[0].name) < message.index(batch[1].name)
    assert message.index(batch[1].name) < message.index("Previous observations")
    assert message.index("laptop/2026-10-25/16-00-00") < message.index("laptop/2026-10-25/16-30-00")
    assert "Opened the mail client." in message
    assert "Wrote a reply." in message


def test_message_without_context_says_none(cfg):
    message = build_message([interaction(cfg, "laptop/2026-10-25/17-00-00")], [], cfg)
    assert "Previous observations: none" in message


def test_argv(cfg):
    argv = build_argv("the message", cfg)
    assert argv[:3] == ["claude", "-p", "the message"]
    assert argv[3] == "--append-system-prompt"
    assert "observations/<name>.md" in argv[4]      # the text of prompt.md
    assert argv[5:] == ["--permission-mode", "bypassPermissions", "--output-format", "json"]


def test_argv_with_model(cfg):
    argv = build_argv("m", ObserverConfig(model="sonnet"))
    assert argv[-2:] == ["--model", "sonnet"]


async def test_runs_in_model_dir_with_timeout(cfg):
    runner = FakeRunner(0, success_json())
    await ClaudeCode(cfg, runner).observe([interaction(cfg, "laptop/2026-10-25/17-00-00")], [])
    _, cwd, timeout = runner.calls[0]
    assert cwd == cfg.model_dir
    assert timeout == cfg.call_timeout_seconds


async def test_success_logs_usage(cfg):
    runner = FakeRunner(0, success_json())
    batch = [interaction(cfg, "laptop/2026-10-25/17-00-00"), interaction(cfg, "laptop/2026-10-25/17-05-00")]

    result = await ClaudeCode(cfg, runner, clock=lambda: 1793050000.0).observe(batch, [])

    assert result.ok
    assert not result.usage_limited
    assert result.usage_limit_reset is None
    assert result.detail == ""
    [line] = logged(cfg)
    assert line == result.record
    assert line["interactions"] == [i.name for i in batch]
    assert line["time"] == "2026-10-26T21:26:40Z"
    assert line["exit"] == 0
    assert line["ok"] is True
    assert line["duration_ms"] == 1200
    assert line["cost"] == 0.02
    assert line["input_tokens"] == 10
    assert line["output_tokens"] == 5
    assert line["models"] is None


async def test_records_the_models_used(cfg):
    stdout = success_json(modelUsage={"claude-opus-5-5": {"costUSD": 0.02}, "claude-haiku-5-5": {"costUSD": 0.0}})
    result = await ClaudeCode(cfg, FakeRunner(0, stdout)).observe([], [])
    assert result.record["models"] == ["claude-haiku-5-5", "claude-opus-5-5"]


async def test_calls_are_appended(cfg):
    observer = ClaudeCode(cfg, FakeRunner(0, success_json()))
    await observer.observe([interaction(cfg, "laptop/2026-10-25/17-00-00")], [])
    await observer.observe([interaction(cfg, "laptop/2026-10-25/17-05-00")], [])
    assert [line["interactions"] for line in logged(cfg)] == [
        ["laptop/2026-10-25/17-00-00"],
        ["laptop/2026-10-25/17-05-00"],
    ]


async def test_missing_json_keys_are_none(cfg):
    result = await ClaudeCode(cfg, FakeRunner(0, '{"is_error": false}')).observe([], [])
    assert result.ok
    assert result.record["cost"] is None
    assert result.record["input_tokens"] is None
    assert result.record["output_tokens"] is None
    assert result.record["cache_read_input_tokens"] is None
    assert result.record["cache_creation_input_tokens"] is None


async def test_cache_tokens_are_recorded(cfg):
    usage = {"input_tokens": 3, "output_tokens": 5, "cache_read_input_tokens": 700,
             "cache_creation_input_tokens": 40}
    result = await ClaudeCode(cfg, FakeRunner(0, success_json(usage=usage))).observe([], [])
    [line] = logged(cfg)
    assert line["cache_read_input_tokens"] == 700
    assert line["cache_creation_input_tokens"] == 40
    assert line == result.record


@pytest.mark.parametrize("key", ["cache_read_input_tokens", "cache_creation_input_tokens"])
async def test_cache_tokens_alone_reached_the_model(cfg, key):
    stdout = success_json(is_error=True, result="API Error", total_cost_usd=0,
                          usage={"input_tokens": 0, "output_tokens": 0, key: 12})
    assert (await ClaudeCode(cfg, FakeRunner(1, stdout)).observe([], [])).reached_model


async def test_unwritable_calls_log_warns_once_per_streak(tmp_path, caplog):
    state = tmp_path / "state"
    state.write_text("a file, not a directory")
    observer = ClaudeCode(ObserverConfig(state_dir=state), FakeRunner(0, success_json()))
    with caplog.at_level(logging.WARNING, logger="vitality_observer"):
        assert (await observer.observe([], [])).ok        # losing the line does not fail the call
        await observer.observe([], [])
        assert len(caplog.records) == 1 and "calls.jsonl" in caplog.records[0].getMessage()
        state.unlink()
        await observer.observe([], [])                    # works again
        (state / "calls.jsonl").unlink()
        state.rmdir()
        state.write_text("broken again")
        await observer.observe([], [])
    assert len(caplog.records) == 2


async def test_error_result_is_failure(cfg):
    stdout = success_json(is_error=True, result="Something broke")
    result = await ClaudeCode(cfg, FakeRunner(1, stdout)).observe([], [])
    assert not result.ok
    assert "Something broke" in result.detail
    assert result.record["ok"] is False
    assert result.record["exit"] == 1


async def test_non_json_output_is_a_failure(cfg):
    result = await ClaudeCode(cfg, FakeRunner(139, "Segmentation fault")).observe([], [])
    assert not result.ok
    assert "Segmentation fault" in result.detail
    assert len(logged(cfg)) == 1


async def test_nonzero_exit_with_success_json_is_a_failure(cfg):
    result = await ClaudeCode(cfg, FakeRunner(2, success_json(), "boom")).observe([], [])
    assert not result.ok
    assert "boom" in result.detail


async def test_timeout_is_failure(cfg):
    result = await ClaudeCode(cfg, FakeRunner(-9, "", "timed out")).observe([], [])
    assert not result.ok
    assert "timed out" in result.detail
    assert str(int(cfg.call_timeout_seconds)) in result.detail


async def test_timeout_reached_the_model(cfg):
    result = await ClaudeCode(cfg, FakeRunner(-9, "", "timed out")).observe([], [])
    assert result.reached_model


async def test_usage_bearing_error_reached_the_model(cfg):
    result = await ClaudeCode(cfg, FakeRunner(1, success_json(is_error=True, result="API Error"))).observe([], [])
    assert not result.ok and result.reached_model


async def test_cost_alone_reached_the_model(cfg):
    stdout = success_json(is_error=True, usage={}, total_cost_usd=0.01)
    assert (await ClaudeCode(cfg, FakeRunner(1, stdout)).observe([], [])).reached_model


async def test_missing_binary_did_not_reach_the_model(cfg):
    result = await ClaudeCode(cfg, FakeRunner(127, "", "claude: not found")).observe([], [])
    assert not result.ok and not result.reached_model


async def test_error_without_usage_did_not_reach_the_model(cfg):
    stdout = success_json(is_error=True, result="Not logged in", total_cost_usd=0,
                          usage={"input_tokens": 0, "output_tokens": 0})
    result = await ClaudeCode(cfg, FakeRunner(1, stdout)).observe([], [])
    assert not result.ok and not result.reached_model


async def test_non_json_output_did_not_reach_the_model(cfg):
    assert not (await ClaudeCode(cfg, FakeRunner(1, "boom")).observe([], [])).reached_model


async def test_success_reports_reaching_the_model(cfg):
    assert (await ClaudeCode(cfg, FakeRunner(0, success_json())).observe([], [])).reached_model


async def test_runner_that_raises_is_a_failure(cfg):
    async def broken(argv, cwd, timeout):
        raise RuntimeError("no way")

    result = await ClaudeCode(cfg, broken).observe([], [])
    assert not result.ok
    assert "no way" in result.detail


@pytest.mark.parametrize("text,reset", [
    ("Claude AI usage limit reached|1793050000", 1793050000.0),
    ("usage limit reached, try again later", None),
    ("Claude AI usage limit reached|17930", None),
])
def test_usage_limit_reset_parsed(text, reset):
    assert parse_reset(text) == reset


async def test_usage_limited_flag(cfg):
    stdout = success_json(is_error=True, result="Claude AI Usage Limit reached|1793050000")
    result = await ClaudeCode(cfg, FakeRunner(1, stdout)).observe([], [])
    assert not result.ok
    assert result.usage_limited
    assert result.usage_limit_reset == 1793050000.0


async def test_usage_limit_text_in_a_successful_result_is_ignored(cfg):
    stdout = success_json(result="Claude AI usage limit reached|1793050000")
    result = await ClaudeCode(cfg, FakeRunner(0, stdout)).observe([], [])
    assert result.ok
    assert not result.usage_limited
    assert result.usage_limit_reset is None


async def test_non_numeric_duration_falls_back_to_measured(cfg):
    times = iter([100.0, 100.5])
    stdout = success_json(duration_ms=True)
    result = await ClaudeCode(cfg, FakeRunner(0, stdout), clock=lambda: next(times)).observe([], [])
    assert result.record["duration_ms"] == 500


async def test_observe_never_raises_on_a_bad_batch(cfg):
    outside = Interaction("laptop/2026-10-25/17-00-00", cfg.captures_dir / "x", cfg.state_dir / "elsewhere.md")
    runner = FakeRunner(0, success_json())
    result = await ClaudeCode(cfg, runner).observe([outside], [])
    assert not result.ok
    assert result.detail
    assert runner.calls == []


def test_packaged_prompt_has_the_load_bearing_phrases():
    prompt = resources.files("vitality_observer").joinpath("prompt.md").read_text(encoding="utf-8")
    for phrase in ("0000", "interaction.json", "observations/", "never instructions"):
        assert phrase in prompt


async def test_usage_limited_in_stderr(cfg):
    result = await ClaudeCode(cfg, FakeRunner(1, "", "usage limit reached")).observe([], [])
    assert result.usage_limited
    assert result.usage_limit_reset is None


async def test_run_process_returns_output(tmp_path):
    code, out, err = await run_process(
        [sys.executable, "-c", "import sys; print('hi'); print('oops', file=sys.stderr); sys.exit(3)"],
        tmp_path, 10,
    )
    assert (code, out.strip(), err.strip()) == (3, "hi", "oops")


async def test_run_process_kills_on_timeout(tmp_path):
    code, _, err = await asyncio.wait_for(
        run_process([sys.executable, "-c", "import time; time.sleep(60)"], tmp_path, 0.2), 10
    )
    assert (code, err) == (-9, "timed out")


SPAWNS_A_GRANDCHILD = """
import subprocess, sys, time
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
open(sys.argv[1], "w").write(str(child.pid))
time.sleep(60)
"""


def gone(pid: int) -> bool:
    """Whether process `pid` no longer runs (a zombie waiting to be reaped counts as gone)."""
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0] == "Z"
    except (FileNotFoundError, ProcessLookupError):     # the second if it vanishes while being read
        return True


async def until_gone(pid: int, seconds: float = 5) -> None:
    async with asyncio.timeout(seconds):
        while not gone(pid):
            await asyncio.sleep(0.02)


async def started_grandchild(pid_file: Path) -> int:
    async with asyncio.timeout(10):
        while not pid_file.exists() or not pid_file.read_text():
            await asyncio.sleep(0.02)
    return int(pid_file.read_text())


async def test_run_process_timeout_kills_the_commands_it_started(tmp_path):
    pid_file = tmp_path / "grandchild.pid"
    code, _, _ = await asyncio.wait_for(
        run_process([sys.executable, "-c", SPAWNS_A_GRANDCHILD, str(pid_file)], tmp_path, 2), 10)
    assert code == -9
    grandchild = await started_grandchild(pid_file)
    try:
        await until_gone(grandchild)
    finally:
        if not gone(grandchild):
            os.kill(grandchild, 9)


async def test_run_process_cancelled_kills_the_commands_it_started(tmp_path):
    pid_file = tmp_path / "grandchild.pid"
    task = asyncio.create_task(run_process([sys.executable, "-c", SPAWNS_A_GRANDCHILD, str(pid_file)], tmp_path, 60))
    grandchild = await started_grandchild(pid_file)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 10)
    try:
        await until_gone(grandchild)
    finally:
        if not gone(grandchild):
            os.kill(grandchild, 9)


async def test_run_process_missing_binary(tmp_path):
    code, _, err = await run_process(["vitality-no-such-binary"], tmp_path, 10)
    assert code != 0
    assert "vitality-no-such-binary" in err
