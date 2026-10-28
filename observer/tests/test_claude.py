import asyncio
import json
import sys
from collections.abc import Sequence
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
    assert "model" not in line


async def test_records_the_configured_model(cfg):
    config = ObserverConfig(state_dir=cfg.state_dir, model="sonnet")
    result = await ClaudeCode(config, FakeRunner(0, success_json())).observe([], [])
    assert result.record["model"] == "sonnet"


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


async def test_run_process_missing_binary(tmp_path):
    code, _, err = await run_process(["vitality-no-such-binary"], tmp_path, 10)
    assert code != 0
    assert "vitality-no-such-binary" in err
