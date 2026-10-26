import os
import subprocess
from pathlib import Path

import pytest

from vitality_client.cli import main
from vitality_client.cli.commands import capture
from vitality_client.cli.upgrade import upgrade

INSTALL = "./client/install.sh"
FIX = "run ./client/install.sh from the repo"


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True)
    return result.stdout.strip()


def commit(repo: Path, subject: str) -> str:
    (repo / "file.txt").write_text(subject)
    git(repo, "add", "file.txt")
    git(repo, "commit", "--quiet", "-m", subject)
    return git(repo, "rev-parse", "HEAD")


class World:
    """A git checkout on main with one commit, standing for the repo, and the app directory
    (`$XDG_DATA_HOME/vitality-client`) that install.sh would have filled: `source` names the
    checkout, `installed` holds the commit installed (the first one)."""

    def __init__(self, tmp_path: Path):
        self.checkout = tmp_path / "repo"
        self.checkout.mkdir()
        subprocess.run(["git", "init", "--quiet", "-b", "main", str(self.checkout)], check=True)
        git(self.checkout, "config", "user.name", "Tester")
        git(self.checkout, "config", "user.email", "tester@example.com")
        self.first = commit(self.checkout, "first")
        self.data_home = tmp_path / "data"
        self.app_dir = self.data_home / "vitality-client"
        self.app_dir.mkdir(parents=True)
        self.source_file = self.app_dir / "source"
        self.installed_file = self.app_dir / "installed"
        self.source_file.write_text(f"{self.checkout}\n")
        self.installed_file.write_text(f"{self.first}\n")
        self.doctor = str(self.app_dir / "venv/bin/vitality-client")
        self.env = {"XDG_DATA_HOME": str(self.data_home)}


class Streamed:
    """Stands for the install and doctor processes: records each call, answers `exit_codes`
    by program (default 0)."""

    def __init__(self):
        self.calls: list[tuple[list[str], Path | None]] = []
        self.exit_codes: dict[str, int] = {}

    def __call__(self, args, cwd):
        self.calls.append((list(args), cwd))
        return self.exit_codes.get(args[0], 0)

    def ran(self, program: str) -> list[tuple[list[str], Path | None]]:
        return [call for call in self.calls if call[0][0] == program]


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    return World(tmp_path)


@pytest.fixture
def streamed():
    return Streamed()


def run(world, streamed):
    return upgrade(world.env, capture=capture, stream=streamed)


def test_root_is_refused(world, streamed, monkeypatch, capsys):
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    assert run(world, streamed) == 1
    assert "not with sudo" in capsys.readouterr().err
    assert streamed.calls == []


def test_no_source_file(world, streamed, capsys):
    world.source_file.unlink()
    assert run(world, streamed) == 1
    err = capsys.readouterr().err
    assert "not installed with client/install.sh yet" in err and FIX in err
    assert streamed.calls == []


def test_empty_source_file_counts_as_missing(world, streamed, capsys):
    world.source_file.write_text("\n")
    assert run(world, streamed) == 1
    assert "not installed with client/install.sh yet" in capsys.readouterr().err


def test_source_that_is_not_a_git_checkout(world, streamed, tmp_path, capsys):
    plain = tmp_path / "plain"
    plain.mkdir()
    world.source_file.write_text(f"{plain}\n")
    assert run(world, streamed) == 1
    err = capsys.readouterr().err
    assert str(plain) in err and "not a git checkout" in err and FIX in err
    assert streamed.calls == []


def test_other_branch_is_refused(world, streamed, capsys):
    git(world.checkout, "switch", "--quiet", "-c", "work")
    assert run(world, streamed) == 1
    err = capsys.readouterr().err
    assert "'work'" in err and "git -C" in err and "switch main" in err
    assert streamed.calls == []


def test_modified_tracked_file_is_refused(world, streamed, capsys):
    (world.checkout / "file.txt").write_text("edited")
    assert run(world, streamed) == 1
    err = capsys.readouterr().err
    assert "uncommitted changes" in err and "nothing was installed" in err
    assert streamed.calls == []


def test_untracked_files_do_not_block(world, streamed):
    commit(world.checkout, "a change")
    (world.checkout / "scratch.txt").write_text("not tracked")
    assert run(world, streamed) == 0
    assert len(streamed.ran(INSTALL)) == 1


def test_already_installed_installs_nothing(world, streamed, capsys):
    assert run(world, streamed) == 0
    sha = git(world.checkout, "rev-parse", "--short", "HEAD")
    assert f"already installed ({sha})" in capsys.readouterr().out
    assert streamed.calls == []


def test_new_commits_are_listed_installed_and_checked(world, streamed, capsys):
    commit(world.checkout, "add the thing")
    commit(world.checkout, "fix the other thing")
    assert run(world, streamed) == 0
    lines = capsys.readouterr().out.splitlines()
    assert "installing 2 new commit(s):" in lines
    first = git(world.checkout, "log", "--format=%h", "-1", "HEAD~1")
    last = git(world.checkout, "log", "--format=%h", "-1", "HEAD")
    assert f"  {last} fix the other thing" in lines and f"  {first} add the thing" in lines
    assert streamed.calls == [([INSTALL], world.checkout), ([world.doctor, "doctor"], None)]
    assert len([line for line in lines if line.startswith("==> ")]) == 4  # one per step


@pytest.mark.parametrize("doctor_code", [0, 1])
def test_upgrade_exit_code_is_the_doctors(world, streamed, doctor_code):
    commit(world.checkout, "a change")
    streamed.exit_codes[world.doctor] = doctor_code
    assert run(world, streamed) == doctor_code
    assert len(streamed.ran(world.doctor)) == 1


def test_missing_installed_file_lists_the_head_commit(world, streamed, capsys):
    world.installed_file.unlink()
    assert run(world, streamed) == 0
    out = capsys.readouterr().out
    head = git(world.checkout, "log", "--format=%h %s", "-1", "HEAD")
    assert f"installing {head}" in out.splitlines()
    assert "new commit(s)" not in out
    assert len(streamed.ran(INSTALL)) == 1


def test_installed_commit_that_is_not_an_ancestor_lists_the_head_commit(world, streamed, capsys):
    git(world.checkout, "switch", "--quiet", "-c", "side")
    side = commit(world.checkout, "side work")
    git(world.checkout, "switch", "--quiet", "main")
    commit(world.checkout, "main work")
    world.installed_file.write_text(f"{side}\n")
    assert run(world, streamed) == 0
    out = capsys.readouterr().out
    head = git(world.checkout, "log", "--format=%h %s", "-1", "HEAD")
    assert f"installing {head}" in out.splitlines()
    assert "new commit(s)" not in out


def test_install_failure_skips_doctor(world, streamed, capsys):
    commit(world.checkout, "a change")
    streamed.exit_codes[INSTALL] = 3
    assert run(world, streamed) == 1
    assert "install.sh failed (exit 3); see its output above" in capsys.readouterr().err
    assert streamed.ran(world.doctor) == []


def test_app_dir_defaults_to_local_share_under_home(world, streamed, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    assert upgrade({}, capture=capture, stream=streamed) == 1
    assert str(tmp_path / "home/.local/share/vitality-client/source") in capsys.readouterr().err


def test_upgrade_is_a_command(world, monkeypatch, capsys):
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    assert main(["upgrade"]) == 1
    assert "not with sudo" in capsys.readouterr().err
