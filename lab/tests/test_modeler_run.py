import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from vitality_modeler.config import ModelerConfig
from vitality_modeler.playbook import PLAYBOOK, PLAYBOOK_VERSION

from vitality_lab.cli import main
from vitality_lab.modeler_run import dataset_kind, docker_build_command, docker_run_command, modeler_settings
from vitality_lab.record import read_run_yaml

STARTED = datetime(2026, 11, 4, 15, 30, 2, tzinfo=timezone(timedelta(hours=-5)))


class FakeRunner:
    """Records commands; a `docker run` writes what the container would."""

    def __init__(self, build_code: int = 0, summary: bool = True):
        self.commands: list[list[str]] = []
        self.build_code = build_code
        self.summary = summary

    def __call__(self, command: list[str]) -> int:
        self.commands.append(command)
        if command[:2] == ["docker", "build"]:
            return self.build_code
        return 0


def run_dir(command: list[str]) -> Path:
    mounts = [command[i + 1] for i, a in enumerate(command) if a == "-v"]
    return Path(next(m for m in mounts if m.endswith(":/run")).removesuffix(":/run"))


class Runner(FakeRunner):
    def __call__(self, command: list[str]) -> int:
        code = super().__call__(command)
        if command[:2] == ["docker", "run"] and self.summary:
            run = run_dir(command)
            (run / "replay.json").write_text(json.dumps(
                {"sessions": {"committed": 2}, "stopped": None, "not_offered": []}))
            (run / "sessions.jsonl").write_text(
                json.dumps({"outcome": "committed", "cost": 0.5}) + "\n"
                + json.dumps({"outcome": "committed", "cost": None}) + "\n"
                + json.dumps({"outcome": "committed", "cost": 0.25}) + "\n")
        return code


@pytest.fixture
def lab(tmp_path: Path) -> Path:
    folder = tmp_path / "lab" / "datasets" / "ev"
    (folder / "operations" / "laptop").mkdir(parents=True)
    (folder / "operations" / "laptop" / "2026-11-01.jsonl").write_text("{}\n")
    (folder / "user-context.md").write_text("context")
    return tmp_path / "lab"


@pytest.fixture
def home(tmp_path: Path) -> Path:
    (tmp_path / "home" / ".claude").mkdir(parents=True)
    (tmp_path / "home" / ".claude" / ".credentials.json").write_text("{}")
    return tmp_path / "home"


def run(repo: Path, lab: Path, home: Path, *args: str, runner: FakeRunner | None = None,
        environ: dict[str, str] | None = None) -> int:
    return main(["run", "modeler", *args], environ={"LAB_DIR": str(lab), **(environ or {})}, repo=repo,
                runner=runner or Runner(), home=home, now=lambda: STARTED)


def runs(lab: Path) -> list[Path]:
    folder = lab / "runs"
    return sorted(folder.iterdir()) if folder.is_dir() else []


def test_modeler_run_writes_record(repo: Path, lab: Path, home: Path):
    runner = Runner()
    environ = {"ANTHROPIC_API_KEY": "sk-secret", "MODELER_EFFORT": "low"}
    assert run(repo, lab, home, "ev", runner=runner, environ=environ) == 0
    [folder] = runs(lab)
    assert (folder / "playbook.md").read_text() == PLAYBOOK
    record = read_run_yaml(folder / "run.yaml")
    assert record["stage"] == "modeler"
    assert record["input"] == "datasets/ev"
    assert record["playbook_version"] == PLAYBOOK_VERSION
    assert record["result"] == {"sessions": {"committed": 2}, "cost_usd": 0.75}
    assert record["stopped"] is None and record["not_offered"] == []
    assert record["finished"] is not None
    assert record["settings"]["effort"] == "low"
    build, launch = runner.commands
    assert build == docker_build_command(repo)
    config = modeler_settings(environ, [])
    expected = docker_run_command(lab / "datasets" / "ev", folder, home / ".claude" / ".credentials.json",
                                  config, os.getuid(), os.getgid())
    assert launch == expected
    assert f"{os.getuid()}:{os.getgid()}" in launch
    assert not any("ANTHROPIC_API_KEY" in arg or "sk-secret" in arg for arg in launch)


def test_commands():
    config = ModelerConfig()
    assert docker_build_command(Path("/r")) == [
        "docker", "build", "-f", "/r/modeler/Dockerfile", "-t", "vitality-modeler", "/r"]
    assert docker_run_command(Path("/d"), Path("/u"), Path("/c"), config, 1000, 1001) == [
        "docker", "run", "--rm", "--user", "1000:1001",
        "-v", "/d:/dataset:ro", "-v", "/u:/run", "-v", "/c:/home/modeler/.claude/.credentials.json",
        "-e", "MODELER_MODEL=claude-opus-5-5", "-e", "MODELER_EFFORT=high",
        "-e", "MODELER_MAX_OPERATIONS=500", "-e", "MODELER_RUN_TIMEOUT_SECONDS=3600",
        "vitality-modeler", "vitality-modeler", "replay", "/dataset", "/run"]


def test_build_failure_recorded(repo: Path, lab: Path, home: Path):
    runner = Runner(build_code=1)
    assert run(repo, lab, home, "ev", runner=runner) == 1
    assert len(runner.commands) == 1
    [folder] = runs(lab)
    record = read_run_yaml(folder / "run.yaml")
    assert record["stopped"] == "image build failed"
    assert record["finished"] is not None and record["result"] is None


def test_missing_summary_recorded(repo: Path, lab: Path, home: Path):
    assert run(repo, lab, home, "ev", runner=Runner(summary=False)) == 1
    [folder] = runs(lab)
    record = read_run_yaml(folder / "run.yaml")
    assert record["stopped"] == "container ended without a summary"
    assert record["finished"] is not None


def test_stopped_run_exits_1(repo: Path, lab: Path, home: Path):
    class Stopping(Runner):
        def __call__(self, command: list[str]) -> int:
            code = super().__call__(command)
            if command[:2] == ["docker", "run"]:
                (run_dir(command) / "replay.json").write_text(json.dumps(
                    {"sessions": {"committed": 1}, "stopped": "usage limit", "not_offered": ["a", "b"]}))
            return code
    assert run(repo, lab, home, "ev", runner=Stopping()) == 1
    record = read_run_yaml(runs(lab)[0] / "run.yaml")
    assert record["stopped"] == "usage limit" and record["not_offered"] == ["a", "b"]


@pytest.mark.parametrize("case", ["captures", "no-context", "no-credentials", "bad-set", "unknown-set", "untracked",
                                  "bad-label"])
def test_refusals(repo: Path, lab: Path, home: Path, case: str, capsys):
    args: list[str] = ["ev"]
    if case == "captures":
        d = lab / "datasets" / "cap" / "captures" / "laptop" / "2026-11-01" / "10-00-00"
        d.mkdir(parents=True)
        (d / "interaction.json").write_text("{}")
        args = ["cap"]
    elif case == "no-context":
        (lab / "datasets" / "ev" / "user-context.md").unlink()
    elif case == "no-credentials":
        (home / ".claude" / ".credentials.json").unlink()
    elif case == "bad-set":
        args += ["--set", "effort=x"]
    elif case == "unknown-set":
        args += ["--set", "mode=x"]
    elif case == "untracked":
        (repo / "modeler").mkdir()
        (repo / "modeler" / "x.py").write_text("")
    elif case == "bad-label":
        args += ["--label", "Bad Label"]
    runner = Runner()
    assert run(repo, lab, home, *args, runner=runner) == 1
    assert runs(lab) == [] and runner.commands == []
    err = capsys.readouterr().err
    if case == "captures":
        assert "is a captures dataset; use run observer" in err
    if case == "unknown-set":
        assert "unknown setting 'mode'; one of model, effort, max_operations, run_timeout_seconds" in err


def test_observer_refuses_evidence_dataset(repo: Path, lab: Path, capsys):
    from test_observer_run import FakeObserver
    code = main(["run", "observer", "ev"], environ={"LAB_DIR": str(lab), "ANTHROPIC_API_KEY": "k"}, repo=repo,
                make_observer=lambda config: FakeObserver(), now=lambda: STARTED)
    assert code == 1 and runs(lab) == []
    assert "is an evidence dataset; use run modeler" in capsys.readouterr().err


def test_dataset_kind(lab: Path):
    assert dataset_kind(lab / "datasets" / "ev") == "evidence"
    assert dataset_kind(lab / "datasets" / "none") is None
    (lab / "datasets" / "empty" / "operations").mkdir(parents=True)
    assert dataset_kind(lab / "datasets" / "empty") is None


def test_list_shows_kinds(repo: Path, lab: Path, home: Path, capsys):
    d = lab / "datasets" / "cap" / "captures" / "laptop" / "2026-11-01" / "10-00-00"
    d.mkdir(parents=True)
    (d / "interaction.json").write_text("{}")
    assert run(repo, lab, home, "ev") == 0
    capsys.readouterr()
    assert main(["list"], environ={"LAB_DIR": str(lab)}) == 0
    out = capsys.readouterr().out
    assert "cap  1 interactions  captures" in out
    assert "ev  1 operations files  evidence" in out
    assert "modeler  datasets/ev  sessions 2/2" in out
