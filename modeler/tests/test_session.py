import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from conftest import add_evidence
from vitality_modeler import session
from vitality_modeler.config import ModelerConfig
from vitality_modeler.session import run_session
from vitality_usermodel.formats import ActionMeta, OperationsMeta, read_file, render

FAKE = str(Path(__file__).parent / "fake_claude.py")
CONFIG = ModelerConfig(run_timeout_seconds=30)
WHEN = datetime(2026, 11, 4, 12, 0, 0, tzinfo=UTC)
FIRST = "laptop/2026-11-03/14-00-00"
SECOND = "laptop/2026-11-03/15-00-00"


def git(model: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(model), *args], capture_output=True, text=True, check=True).stdout


def with_evidence(model: Path) -> Path:
    add_evidence(model, FIRST, "2026-11-03T09:00:00-05:00", 2)
    add_evidence(model, SECOND, "2026-11-03T10:00:00-05:00", 1)
    return model


def action(model: Path, *, skip: int = 0) -> dict[str, str]:
    """A `write` step: one action covering every waiting operation except the last `skip`."""
    metas: list[OperationsMeta] = []
    for path in sorted((model / "operations").rglob("*.md")):
        meta, _ = read_file(path, "operations")
        assert isinstance(meta, OperationsMeta)
        metas.append(meta)
    refs = [f"{m.interaction}#{op.id}" for m in metas for op in m.operations]
    meta = ActionMeta(id="2026-11-03/14-00-00", summary="Did things", start=min(m.start for m in metas),
                      end=max(m.end for m in metas), operations=refs[:len(refs) - skip], confidence=6)
    return {"actions/2026-11-03/14-00-00.md": render(meta, "What was done.\n")}


def run(model: Path, records: Path, steps: list[dict[str, Any]], *, config: ModelerConfig = CONFIG,
        environ: dict[str, str] | None = None) -> session.SessionRecord | None:
    script = records.parent / "script.json"
    script.write_text(json.dumps(steps))
    return run_session(model, records, config, "the playbook", "abc123", trigger="replay", claude=FAKE,
                       environ={**(environ or {}), "PATH": "/usr/bin:/bin", "FAKE_CLAUDE_SCRIPT": str(script)},
                       clock=lambda: WHEN)


def calls(records: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (records.parent / "script.json.calls").read_text().splitlines()]


def lines(records: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (records / "sessions.jsonl").read_text().splitlines()]


@pytest.fixture
def records(tmp_path: Path) -> Path:
    return tmp_path / "records"


def assert_reverted(model: Path, start: str, before: dict[str, bytes]) -> None:
    assert git(model, "rev-parse", "HEAD").strip() == start
    assert git(model, "status", "--porcelain", "--untracked-files=all").splitlines() == sorted(
        f"?? {p}" for p in before)
    assert {p: (model / p).read_bytes() for p in before} == before


def evidence_bytes(model: Path) -> dict[str, bytes]:
    return {str(p.relative_to(model)): p.read_bytes()
            for d in ("operations", "observations") for p in (model / d).rglob("*.md")}


def test_nothing_waiting_returns_none(model: Path, records: Path):
    assert run(model, records, [{}]) is None
    assert not (records / "sessions.jsonl").exists()


def test_refuses_foreign_changes(model: Path, records: Path):
    with_evidence(model)
    (model / "user-context.md").write_text("edited\n")
    with pytest.raises(RuntimeError, match="user-context.md"):
        run(model, records, [{}])


def test_committed(model: Path, records: Path):
    with_evidence(model)
    start = git(model, "rev-parse", "HEAD").strip()
    record = run(model, records, [{"write": action(model)}])
    assert record is not None
    assert record.outcome == "committed"
    assert record.attempts == 1
    assert git(model, "rev-list", "--count", f"{start}..HEAD").strip() == "1"
    assert git(model, "log", "-1", "--format=%s").startswith("modeler run ")
    assert git(model, "log", "-1", "--format=%s").strip() == \
        "modeler run 2026-11-04T12-00-00Z: 1 actions, 0 activities, 0 strivings"
    assert record.commit == git(model, "rev-parse", "HEAD").strip()
    assert record.changes["actions"] == 1
    assert record.cost == 0.5
    assert (record.input_tokens, record.output_tokens) == (100, 10)
    assert record.operations == {"count": 3, "from": FIRST, "to": SECOND}
    assert (record.run, record.trigger, record.playbook, record.model) == \
        ("2026-11-04T12-00-00Z", "replay", "abc123", CONFIG.model)
    assert (record.started, record.ended, record.duration_ms) == ("2026-11-04T12:00:00Z", "2026-11-04T12:00:00Z", 0)
    assert record.transcript == "transcripts/2026-11-04T12-00-00Z.jsonl"
    assert (records / record.transcript).read_text().strip()
    assert lines(records) == [record.to_json()]
    assert git(model, "status", "--porcelain").strip() == ""
    argv = calls(records)[0]["argv"]
    assert argv[argv.index("--append-system-prompt") + 1] == "the playbook"
    message = argv[argv.index("-p") + 1]
    assert f"operations/{FIRST}.md (starts 2026-11-03T09:00-05:00)" in message


def test_fixed_on_resume(model: Path, records: Path):
    with_evidence(model)
    record = run(model, records, [{"write": action(model, skip=1)}, {"write": action(model)}])
    assert record is not None
    assert (record.outcome, record.attempts) == ("committed", 2)
    second = calls(records)[1]["argv"]
    assert second[second.index("--resume") + 1] == "fake-1"
    assert "Fix these, then run vitality-usermodel check." in second[second.index("-p") + 1]
    assert record.cost == 1.0
    assert len((records / record.transcript).read_text().splitlines()) == 4


def test_reverted_after_two_failures(model: Path, records: Path):
    with_evidence(model)
    start = git(model, "rev-parse", "HEAD").strip()
    before = evidence_bytes(model)
    record = run(model, records, [{"write": action(model, skip=1)}])
    assert record is not None
    assert (record.outcome, record.attempts, record.commit) == ("reverted", 2, None)
    assert record.check_errors and all(": " in e for e in record.check_errors)
    assert set(record.changes.values()) == {0}
    assert_reverted(model, start, before)
    assert lines(records) == [record.to_json()]


def test_edited_evidence_restored(model: Path, records: Path):
    with_evidence(model)
    start = git(model, "rev-parse", "HEAD").strip()
    before = evidence_bytes(model)
    record = run(model, records, [{"write": {f"operations/{FIRST}.md": "tampered\n"}, "exit": 1, "is_error": True,
                                   "result": "crashed"}])
    assert record is not None
    assert record.outcome == "failed"
    assert "crashed" in record.detail
    assert_reverted(model, start, before)


def test_limited(model: Path, records: Path):
    with_evidence(model)
    start = git(model, "rev-parse", "HEAD").strip()
    before = evidence_bytes(model)
    record = run(model, records, [{"write": action(model), "result": "usage limit reached", "is_error": True}])
    assert record is not None
    assert record.outcome == "limited"
    assert_reverted(model, start, before)


def test_timeout(model: Path, records: Path):
    with_evidence(model)
    start = git(model, "rev-parse", "HEAD").strip()
    before = evidence_bytes(model)
    record = run(model, records, [{"sleep": 5, "write": action(model)}],
                 config=ModelerConfig(run_timeout_seconds=1))
    assert record is not None
    assert record.outcome == "failed"
    assert "timed out" in record.detail
    assert_reverted(model, start, before)


def test_no_result_event(model: Path, records: Path):
    with_evidence(model)
    record = run(model, records, [{"no_result": True}])
    assert record is not None
    assert record.outcome == "failed"
    assert "no result" in record.detail
    assert (record.input_tokens, record.output_tokens, record.cost) == (None, None, None)
    assert lines(records) == [record.to_json()]


def test_key_not_passed(model: Path, records: Path):
    with_evidence(model)
    run(model, records, [{"write": action(model)}], environ={"ANTHROPIC_API_KEY": "secret"})
    assert calls(records)[0]["api_key"] is False


def test_interrupt(model: Path, records: Path, monkeypatch: pytest.MonkeyPatch):
    with_evidence(model)
    start = git(model, "rev-parse", "HEAD").strip()
    before = evidence_bytes(model)
    real_wait = subprocess.Popen.wait
    raised: list[bool] = []

    def wait(self: subprocess.Popen[str], timeout: float | None = None) -> int:
        if not raised and FAKE in str(self.args):  # git also waits on its processes
            raised.append(True)
            raise KeyboardInterrupt
        return real_wait(self, timeout)

    monkeypatch.setattr(subprocess.Popen, "wait", wait)
    with pytest.raises(KeyboardInterrupt):
        run(model, records, [{"sleep": 2, "write": action(model)}])
    assert_reverted(model, start, before)
    [line] = lines(records)
    assert (line["outcome"], line["detail"]) == ("failed", "interrupted")


def test_commit_failure_reverted(model: Path, records: Path, monkeypatch: pytest.MonkeyPatch):
    with_evidence(model)
    start = git(model, "rev-parse", "HEAD").strip()
    before = evidence_bytes(model)

    def commit_all(model: Path, message: str) -> str:
        raise RuntimeError("boom")

    monkeypatch.setattr(session.repo, "commit_all", commit_all)
    with pytest.raises(RuntimeError, match="boom"):
        run(model, records, [{"write": action(model)}])
    assert_reverted(model, start, before)
    [line] = lines(records)
    assert (line["outcome"], line["detail"], line["commit"]) == ("failed", "RuntimeError: boom", None)
    assert set(line["changes"].values()) == {0}


def test_interrupt_after_staging(model: Path, records: Path, monkeypatch: pytest.MonkeyPatch):
    with_evidence(model)
    start = git(model, "rev-parse", "HEAD").strip()
    before = evidence_bytes(model)
    real_stage = session.repo.stage

    def stage(model: Path, offered: session.repo.Offered) -> None:
        real_stage(model, offered)
        raise KeyboardInterrupt

    monkeypatch.setattr(session.repo, "stage", stage)
    with pytest.raises(KeyboardInterrupt):
        run(model, records, [{"write": action(model)}])
    assert_reverted(model, start, before)
    [line] = lines(records)
    assert (line["outcome"], line["detail"], line["attempts"]) == ("failed", "interrupted", 0)


def broken_revert(model: Path, start: str, offered: session.repo.Offered) -> None:
    raise RuntimeError("revert broke")


def test_revert_failure_in_handler_recorded(model: Path, records: Path, monkeypatch: pytest.MonkeyPatch):
    with_evidence(model)

    def commit_all(model: Path, message: str) -> str:
        raise RuntimeError("boom")

    monkeypatch.setattr(session.repo, "commit_all", commit_all)
    monkeypatch.setattr(session.repo, "revert", broken_revert)
    with pytest.raises(RuntimeError, match="revert broke"):
        run(model, records, [{"write": action(model)}])
    [line] = lines(records)
    assert line["outcome"] == "failed"
    assert "RuntimeError: boom" in line["detail"] and "revert broke" in line["detail"]


def test_revert_failure_after_failed_run_recorded(model: Path, records: Path, monkeypatch: pytest.MonkeyPatch):
    with_evidence(model)
    monkeypatch.setattr(session.repo, "revert", broken_revert)
    with pytest.raises(RuntimeError, match="revert broke"):
        run(model, records, [{"exit": 1, "is_error": True, "result": "crashed"}])
    [line] = lines(records)
    assert line["outcome"] == "failed"
    assert "crashed" in line["detail"] and "revert broke" in line["detail"]
