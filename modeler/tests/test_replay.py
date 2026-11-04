import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from conftest import add_evidence
from vitality_modeler import replay as replay_module
from vitality_modeler.config import ModelerConfig
from vitality_modeler.main import main
from vitality_modeler.playbook import PLAYBOOK, PLAYBOOK_VERSION
from vitality_modeler.replay import MODELER_NOTES_TEMPLATE, replay
from vitality_usermodel.formats import ActionMeta, render

FAKE = Path(__file__).parent / "fake_claude.py"
COMMAND = Path(sys.executable).parent / "vitality-modeler"
CONFIG = ModelerConfig(max_operations=6, run_timeout_seconds=30)
FILES = [f"laptop/2026-11-03/{h}-00-00" for h in (14, 15, 16, 17)]  # 09:00 .. 12:00 local
FAILING = {"exit": 1, "is_error": True, "result": "crashed"}


def git(model: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(model), *args], capture_output=True, text=True, check=True).stdout


@pytest.fixture
def dataset(tmp_path: Path) -> Path:
    """Four interactions of three operations each, an hour apart, and the owner's context."""
    folder = tmp_path / "dataset"
    # Written out of order: replay must sort by `start`, not by when files were made.
    for i in (2, 0, 3, 1):
        add_evidence(folder, FILES[i], f"2026-11-03T{9 + i:02d}:00:00-05:00", 3)
    (folder / "user-context.md").write_text("I am a student.\n")
    return folder


def action(*names: str) -> dict[str, str]:
    """A `write` step: one action covering every operation of these interactions."""
    first = datetime.fromisoformat(f"2026-11-03T{9 + FILES.index(names[0]):02d}:00:00-05:00")
    last = datetime.fromisoformat(f"2026-11-03T{9 + FILES.index(names[-1]):02d}:00:00-05:00")
    ident = names[0].split("/", 1)[1]
    meta = ActionMeta(id=ident, summary="Did things", start=first, end=last,
                      operations=[f"{n}#op{i}" for n in names for i in (1, 2, 3)], confidence=6)
    return {f"actions/{ident}.md": render(meta, "What was done.\n")}


def script(tmp_path: Path, steps: list[dict[str, Any]]) -> dict[str, str]:
    path = tmp_path / "script.json"
    path.write_text(json.dumps(steps))
    return {"PATH": "/usr/bin:/bin", "FAKE_CLAUDE_SCRIPT": str(path)}


def calls(tmp_path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (tmp_path / "script.json.calls").read_text().splitlines()]


def offered(call: dict[str, Any]) -> list[str]:
    message = call["argv"][call["argv"].index("-p") + 1]
    return [line.split(" ")[1] for line in message.splitlines() if line.startswith("- operations/")]


def outcomes(run: Path) -> list[tuple[str, str]]:
    return [(r["outcome"], r["detail"]) for r in
            map(json.loads, (run / "sessions.jsonl").read_text().splitlines())]


def test_replay_two_batches(dataset: Path, tmp_path: Path):
    run = tmp_path / "run"
    environ = script(tmp_path, [{"write": action(*FILES[:2])}, {"write": action(*FILES[2:])}])
    summary = replay(dataset, run, CONFIG, claude=str(FAKE), environ=environ)
    assert summary.sessions == {"committed": 2}
    assert summary.stopped is None
    assert summary.not_offered == []
    model = run / "model"
    subjects = git(model, "log", "--reverse", "--format=%s").splitlines()
    assert subjects[0] == "model: start"
    assert len(subjects) == 3 and all(s.startswith("modeler run ") for s in subjects[1:])
    assert (model / "user-context.md").read_text() == "I am a student.\n"
    assert (model / "modeler-notes.md").read_text() == MODELER_NOTES_TEMPLATE
    assert git(model, "status", "--porcelain", "--untracked-files=all") == ""
    for name in FILES:
        assert (model / "observations" / f"{name}.md").read_bytes() == \
            (dataset / "observations" / f"{name}.md").read_bytes()
    assert [offered(c) for c in calls(tmp_path)] == [[f"operations/{n}.md" for n in FILES[:2]],
                                                    [f"operations/{n}.md" for n in FILES[2:]]]
    argv = calls(tmp_path)[0]["argv"]
    assert argv[argv.index("--append-system-prompt") + 1] == PLAYBOOK
    assert {json.loads(line)["playbook"] for line in (run / "sessions.jsonl").read_text().splitlines()} == \
        {PLAYBOOK_VERSION}
    assert json.loads((run / "replay.json").read_text()) == summary.to_json() == \
        {"sessions": {"committed": 2}, "stopped": None, "not_offered": []}


def test_replay_retries_then_commits(dataset: Path, tmp_path: Path):
    run = tmp_path / "run"
    environ = script(tmp_path, [FAILING, {"write": action(*FILES[:2])}, FAILING, FAILING,
                                {"write": action(*FILES[2:])}])
    summary = replay(dataset, run, CONFIG, claude=str(FAKE), environ=environ)
    assert summary.sessions == {"failed": 3, "committed": 2}
    assert summary.stopped is None


def test_replay_retries_then_stops(dataset: Path, tmp_path: Path):
    run = tmp_path / "run"
    summary = replay(dataset, run, CONFIG, claude=str(FAKE), environ=script(tmp_path, [FAILING]))
    assert summary.sessions == {"failed": 3}
    assert summary.stopped == "3 sessions in a row without a commit"
    assert [offered(c) for c in calls(tmp_path)] == [[f"operations/{n}.md" for n in FILES[:2]]] * 3
    assert summary.not_offered == FILES
    assert git(run / "model", "log", "--format=%s").splitlines() == ["model: start"]
    assert json.loads((run / "replay.json").read_text())["stopped"] == "3 sessions in a row without a commit"


def test_replay_limited_stops(dataset: Path, tmp_path: Path):
    run = tmp_path / "run"
    environ = script(tmp_path, [{"write": action(*FILES[:2])},
                                {"is_error": True, "result": "You've hit your weekly limit \u00b7 resets Nov 7"}])
    summary = replay(dataset, run, CONFIG, claude=str(FAKE), environ=environ)
    assert summary.sessions == {"committed": 1, "limited": 1}
    assert summary.stopped == "usage limit reached"
    assert summary.not_offered == FILES[2:]
    assert len(calls(tmp_path)) == 2


def test_replay_interrupt(dataset: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    run = tmp_path / "run"
    real_wait = subprocess.Popen.wait

    def wait(self: subprocess.Popen[str], timeout: float | None = None) -> int:
        if str(FAKE) in str(self.args):
            raise KeyboardInterrupt
        return real_wait(self, timeout)

    monkeypatch.setattr(subprocess.Popen, "wait", wait)
    summary = replay(dataset, run, CONFIG, claude=str(FAKE),
                     environ=script(tmp_path, [{"sleep": 2, "write": action(*FILES[:2])}]))
    assert summary.sessions == {"failed": 1}
    assert summary.stopped == "interrupted"
    assert summary.not_offered == FILES
    assert outcomes(run) == [("failed", "interrupted")]
    assert json.loads((run / "replay.json").read_text())["stopped"] == "interrupted"


def test_replay_error_is_recorded_and_raised(dataset: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    run = tmp_path / "run"

    def run_session(*args: object, **kwargs: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(replay_module, "run_session", run_session)
    with pytest.raises(RuntimeError, match="boom"):
        replay(dataset, run, CONFIG, claude=str(FAKE), environ=script(tmp_path, [{}]))
    assert json.loads((run / "replay.json").read_text()) == \
        {"sessions": {}, "stopped": "RuntimeError: boom", "not_offered": FILES}


def test_replay_refuses_existing_model(dataset: Path, tmp_path: Path):
    (tmp_path / "run" / "model").mkdir(parents=True)
    with pytest.raises(ValueError, match="already exists"):
        replay(dataset, tmp_path / "run", CONFIG, claude=str(FAKE), environ=script(tmp_path, [{}]))


def test_replay_needs_user_context(dataset: Path, tmp_path: Path):
    (dataset / "user-context.md").unlink()
    with pytest.raises(ValueError, match="user-context.md"):
        replay(dataset, tmp_path / "run", CONFIG, claude=str(FAKE), environ=script(tmp_path, [{}]))
    assert not (tmp_path / "run" / "model").exists()


def fake_on_path(tmp_path: Path) -> str:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "claude").symlink_to(FAKE)
    return f"{bin_dir}:/usr/bin:/bin"


def test_main_usage_and_exit_codes(dataset: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                   capsys: pytest.CaptureFixture[str]):
    assert main([]) == 2
    assert main(["replay", str(dataset)]) == 2
    assert main(["other", str(dataset), str(tmp_path / "x")]) == 2
    monkeypatch.setenv("MODELER_EFFORT", "huge")
    assert main(["replay", str(dataset), str(tmp_path / "x")]) == 2
    assert "MODELER_EFFORT" in capsys.readouterr().err
    monkeypatch.delenv("MODELER_EFFORT")
    assert main(["replay", str(tmp_path / "missing"), str(tmp_path / "x")]) == 2
    assert "user-context.md" in capsys.readouterr().err

    monkeypatch.setenv("MODELER_MAX_OPERATIONS", "6")
    monkeypatch.setenv("PATH", fake_on_path(tmp_path))
    for key, value in script(tmp_path, [{"write": action(*FILES[:2])}, {"write": action(*FILES[2:])}]).items():
        if key != "PATH":
            monkeypatch.setenv(key, value)
    before = signal.getsignal(signal.SIGTERM)
    assert main(["replay", str(dataset), str(tmp_path / "ok")]) == 0
    assert signal.getsignal(signal.SIGTERM) == before
    assert json.loads((tmp_path / "ok" / "replay.json").read_text())["sessions"] == {"committed": 2}

    (tmp_path / "script.json").write_text(json.dumps([FAILING]))
    (tmp_path / "script.json.state").unlink()
    assert main(["replay", str(dataset), str(tmp_path / "stopped")]) == 1


def test_sigterm_stops_replay(dataset: Path, tmp_path: Path):
    environ = {**os.environ, **script(tmp_path, [{"sleep": 30, "write": action(*FILES[:2])}]),
               "PATH": fake_on_path(tmp_path), "MODELER_MAX_OPERATIONS": "6"}
    run = tmp_path / "run"
    process = subprocess.Popen([str(COMMAND), "replay", str(dataset), str(run)], env=environ,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 20
        while not (tmp_path / "script.json.calls").exists():
            assert process.poll() is None, process.communicate()
            assert time.monotonic() < deadline, "the fake claude never started"
            time.sleep(0.05)
        started = time.monotonic()
        process.send_signal(signal.SIGTERM)
        _, stderr = process.communicate(timeout=20)
    finally:
        if process.poll() is None:
            process.kill()
    assert process.returncode == 1, stderr
    assert time.monotonic() - started < 10  # the sleeping session was killed, not waited out
    assert json.loads((run / "replay.json").read_text()) == \
        {"sessions": {"failed": 1}, "stopped": "interrupted", "not_offered": FILES}
    assert outcomes(run) == [("failed", "interrupted")]
    assert git(run / "model", "status", "--porcelain", "--untracked-files=all").splitlines() == sorted(
        f"?? {d}/{n}.md" for d in ("observations", "operations") for n in FILES[:2])
