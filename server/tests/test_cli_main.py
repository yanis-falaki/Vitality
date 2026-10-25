import importlib
import tomllib
from pathlib import Path

import pytest
from vitality_server.daemon import main as serverd
from vitality_server.cli import main as run_cli
from vitality_server.shared.config import DEFAULT_CONFIG

cli_module = importlib.import_module("vitality_server.cli.main")  # `cli.main` is the function
PYPROJECT = Path(__file__).parent.parent / "pyproject.toml"


def test_entry_points():
    scripts = tomllib.loads(PYPROJECT.read_text())["project"]["scripts"]
    assert scripts == {"vitality-serverd": "vitality_server.daemon.main:main",
                       "vitality-server": "vitality_server.cli:main"}


def test_default_config_is_shared():
    assert DEFAULT_CONFIG == Path("/etc/vitality/server.toml")
    assert serverd.DEFAULT_CONFIG is DEFAULT_CONFIG


def test_serverd_names_itself_vitality_serverd(capsys):
    with pytest.raises(SystemExit):
        serverd.main(["--help"])
    assert "usage: vitality-serverd" in capsys.readouterr().out


@pytest.mark.parametrize("command", ["status", "doctor", "upgrade"])
def test_commands_dispatch_with_the_default_config(monkeypatch, command):
    seen = []
    monkeypatch.setattr(cli_module, command, lambda config: seen.append(config) or 7)
    assert run_cli([command]) == 7
    assert seen == [DEFAULT_CONFIG]


@pytest.mark.parametrize("command", ["status", "doctor", "upgrade"])
def test_config_option(monkeypatch, command):
    seen = []
    monkeypatch.setattr(cli_module, command, lambda config: seen.append(config) or 0)
    run_cli([command, "--config", "/tmp/x.toml"])
    assert seen == [Path("/tmp/x.toml")]


def test_no_command_exits_2_with_usage(capsys):
    with pytest.raises(SystemExit) as exit_info:
        run_cli([])
    assert exit_info.value.code == 2
    assert "usage: vitality-server" in capsys.readouterr().err


def test_package_exports_main():
    import vitality_server.cli
    assert vitality_server.cli.main is cli_module.main
