import importlib
import tomllib
from pathlib import Path

import pytest
from vitality_server.cli import main as run_cli
from vitality_server.shared.config import ServerConfig

cli_module = importlib.import_module("vitality_server.cli.main")  # `cli.main` is the function
PYPROJECT = Path(__file__).parent.parent / "pyproject.toml"


def test_entry_points():
    scripts = tomllib.loads(PYPROJECT.read_text())["project"]["scripts"]
    assert scripts == {"vitality-server": "vitality_server.cli:main"}


@pytest.mark.parametrize("command", ["serve", "status", "healthcheck"])
def test_dispatches_each_subcommand(monkeypatch, command):
    monkeypatch.setenv("SERVER_PORT", "9123")
    monkeypatch.setenv("SERVER_DATA_DIR", "/somewhere")
    seen = []
    for name in ["serve", "status", "healthcheck"]:
        monkeypatch.setattr(cli_module, name, lambda config, name=name: seen.append((name, config)) or 7)
    assert run_cli([command]) == 7
    assert seen == [(command, ServerConfig(data_dir=Path("/somewhere"), port=9123))]


def test_bad_setting_exits_1_naming_the_variable(monkeypatch, capsys):
    monkeypatch.setenv("SERVER_PORT", "abc")
    monkeypatch.setattr(cli_module, "status", lambda config: pytest.fail("must not run"))
    assert run_cli(["status"]) == 1
    err = capsys.readouterr().err
    assert err.startswith("vitality-server: ") and "SERVER_PORT" in err


def test_no_subcommand_is_a_usage_error(capsys):
    with pytest.raises(SystemExit) as exit_info:
        run_cli([])
    assert exit_info.value.code == 2
    assert "usage: vitality-server" in capsys.readouterr().err


def test_package_exports_main():
    import vitality_server.cli
    assert vitality_server.cli.main is cli_module.main
