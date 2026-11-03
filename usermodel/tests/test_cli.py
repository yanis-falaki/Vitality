from pathlib import Path

import pytest
from conftest import ACTIVITY, edit

from vitality_usermodel.cli import main


def test_cli_ok(model: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["check", "--model-dir", str(model)]) == 0
    assert capsys.readouterr().out == "ok\n"


def test_cli_problems(model: Path, capsys: pytest.CaptureFixture[str]) -> None:
    edit(model, ACTIVITY, "status: tentative", "status: done")
    assert main(["check", "--since", "HEAD", "--model-dir", str(model)]) == 1
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith(f"{ACTIVITY}: status: ")
    assert len(out) == 1


def test_cli_default_model_dir(
    model: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(model)
    assert main(["check"]) == 0
    assert capsys.readouterr().out == "ok\n"


def test_cli_error(model: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["check", "--since", "nope", "--model-dir", str(model)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("vitality-usermodel: ")


def test_cli_next_id(model: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["next-id", "activity", "--model-dir", str(model)]) == 0
    assert main(["next-id", "striving", "--model-dir", str(model)]) == 0
    assert capsys.readouterr().out == "act-0002\nstr-0002\n"
