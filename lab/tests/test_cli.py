from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from test_observer_run import FakeObserver
from vitality_observer.config import ObserverConfig
from vitality_observer.request import PROMPT, PROMPT_VERSION

from vitality_lab.cli import main
from vitality_lab.record import read_run_yaml

STARTED = datetime(2026, 11, 4, 15, 30, 2, tzinfo=timezone(timedelta(hours=-5)))


def make_dataset(lab: Path, name: str, count: int) -> None:
    for i in range(1, count + 1):
        folder = lab / "datasets" / name / "captures" / "laptop" / f"2026-11-0{i}" / "10-00-00"
        folder.mkdir(parents=True)
        (folder / "interaction.json").write_text("{}")


@pytest.fixture
def lab(tmp_path: Path) -> Path:
    lab = tmp_path / "lab"
    make_dataset(lab, "spread-3", 3)
    return lab


def run(repo: Path, lab: Path, *args: str, observer: FakeObserver | None = None, key: str = "sk-test") -> int:
    fake = observer or FakeObserver()
    def make(config: ObserverConfig) -> FakeObserver:
        return fake
    return main(list(args), environ={"LAB_DIR": str(lab), "ANTHROPIC_API_KEY": key}, repo=repo,
                make_observer=make, now=lambda: STARTED)


def runs(lab: Path) -> list[Path]:
    folder = lab / "runs"
    return sorted(folder.iterdir()) if folder.is_dir() else []


def test_run_writes_folder(repo: Path, lab: Path):
    assert run(repo, lab, "run", "observer", "spread-3") == 0
    [folder] = runs(lab)
    assert folder.name.startswith("2026-11-04T15-30-02-") and len(folder.name) == len("2026-11-04T15-30-02-") + 7
    assert (folder / "prompt.md").read_text() == PROMPT
    assert not (folder / "changes.diff").exists()
    record = read_run_yaml(folder / "run.yaml")
    assert record["stage"] == "observer"
    assert record["input"] == "datasets/spread-3"
    assert record["dirty"] is False
    assert record["result"] == {"ok": 3, "failed": 0, "not_run": 0, "cost_usd": 0.06}
    assert record["prompt_version"] == PROMPT_VERSION
    assert record["settings"]["effort"] == "low"
    assert record["started"] == "2026-11-04T15:30:02-05:00"
    assert record["stopped"] is None


def test_dirty_run_saves_diff(repo: Path, lab: Path):
    (repo / "observer" / "core.py").write_text("x = 2\n")
    assert run(repo, lab, "run", "observer", "spread-3") == 0
    [folder] = runs(lab)
    assert folder.name.endswith("-dirty")
    assert "+x = 2" in (folder / "changes.diff").read_text()
    assert read_run_yaml(folder / "run.yaml")["dirty"] is True


def test_label_and_set(repo: Path, lab: Path):
    assert run(repo, lab, "run", "observer", "spread-3", "--label", "try-1", "--set", "effort=high") == 0
    [folder] = runs(lab)
    assert folder.name.endswith("-try-1")
    assert read_run_yaml(folder / "run.yaml")["settings"]["effort"] == "high"


@pytest.mark.parametrize("case", ["missing", "empty", "no-key", "untracked", "label", "setting"])
def test_refusals(repo: Path, lab: Path, case: str, capsys: pytest.CaptureFixture[str]):
    args = ["run", "observer", "spread-3"]
    key = "sk-test"
    if case == "missing":
        args[2] = "nope"
    elif case == "empty":
        (lab / "datasets" / "empty" / "captures").mkdir(parents=True)
        args[2] = "empty"
    elif case == "no-key":
        key = ""
    elif case == "untracked":
        (repo / "lab").mkdir()
        (repo / "lab" / "x.py").write_text("")
    elif case == "label":
        args += ["--label", "a b"]
    else:
        args += ["--set", "mode=batch"]
    assert run(repo, lab, *args, key=key) == 1
    assert runs(lab) == []
    if case == "untracked":
        assert "lab/x.py" in capsys.readouterr().err


def test_stopped_run_exits_1(repo: Path, lab: Path):
    observer = FakeObserver({"laptop/2026-11-02/10-00-00": [(False, "out of API credits", 21600)]})
    assert run(repo, lab, "run", "observer", "spread-3", "--set", "parallel=1", observer=observer) == 1
    record = read_run_yaml(runs(lab)[0] / "run.yaml")
    assert record["stopped"] == "out of API credits"
    assert record["not_run"] == ["laptop/2026-11-02/10-00-00", "laptop/2026-11-03/10-00-00"]
    assert record["result"]["not_run"] == 2


def test_list(repo: Path, lab: Path, capsys: pytest.CaptureFixture[str]):
    run(repo, lab, "run", "observer", "spread-3")
    capsys.readouterr()
    assert run(repo, lab, "list") == 0
    out = capsys.readouterr().out
    assert "spread-3  3 interactions" in out
    assert "ok 3 failed 0" in out


def test_list_empty_lab(repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    assert run(repo, tmp_path / "none", "list") == 0
    assert capsys.readouterr().out == "datasets:\nruns:\n"
