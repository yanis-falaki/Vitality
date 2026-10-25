import os
import subprocess
from pathlib import Path

import pytest
from vitality_server.cli import upgrade as upgrade_module
from vitality_server.cli.commands import capture
from vitality_server.cli.upgrade import upgrade
from vitality_server.shared.config import DEFAULT_CONFIG

PROGRAM = "/opt/vitality-server/bin/vitality-server"
CLONE_FIX = "git clone https://github.com/yanis-falaki/Vitality.git ~/Vitality && cd ~/Vitality && ./server/install.sh"


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True)
    return result.stdout.strip()


def clone(origin: Path, where: Path) -> Path:
    subprocess.run(["git", "clone", "--quiet", str(origin), str(where)], check=True, capture_output=True)
    git(where, "config", "user.name", "Tester")
    git(where, "config", "user.email", "tester@example.com")
    return where


def commit(repo: Path, subject: str) -> None:
    (repo / "file.txt").write_text(subject)
    git(repo, "add", "file.txt")
    git(repo, "commit", "--quiet", "-m", subject)


class World:
    """A bare `origin`, the VM's `checkout` of it (on main), and a second clone, `elsewhere`,
    that stands for the laptop pushing to GitHub."""

    def __init__(self, tmp_path: Path):
        self.origin = tmp_path / "origin.git"
        subprocess.run(["git", "init", "--quiet", "--bare", "-b", "main", str(self.origin)], check=True)
        self.elsewhere = clone(self.origin, tmp_path / "elsewhere")
        git(self.elsewhere, "checkout", "--quiet", "-b", "main")
        commit(self.elsewhere, "first")
        git(self.elsewhere, "push", "--quiet", "origin", "main")
        self.checkout = clone(self.origin, tmp_path / "checkout")
        self.source_file = tmp_path / "source"
        self.source_file.write_text(f"{self.checkout}\n")

    def push(self, subject: str) -> None:
        commit(self.elsewhere, subject)
        git(self.elsewhere, "push", "--quiet", "origin", "main")


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
    world = World(tmp_path)
    monkeypatch.setattr(upgrade_module, "SOURCE_FILE", world.source_file)
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    return world


@pytest.fixture
def streamed():
    return Streamed()


def run(streamed, config=DEFAULT_CONFIG):
    return upgrade(config, capture=capture, stream=streamed)


def test_root_is_refused(world, streamed, monkeypatch, capsys):
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    assert run(streamed) == 1
    assert "not with sudo" in capsys.readouterr().err
    assert streamed.calls == []


def test_no_source_file(world, streamed, capsys):
    world.source_file.unlink()
    assert run(streamed) == 1
    err = capsys.readouterr().err
    assert "don't know where the repo is" in err and "./server/install.sh" in err
    assert streamed.calls == []


def test_source_that_is_not_a_git_checkout(world, streamed, tmp_path, capsys):
    plain = tmp_path / "plain"
    plain.mkdir()
    world.source_file.write_text(f"{plain}\n")
    assert run(streamed) == 1
    err = capsys.readouterr().err
    assert str(plain) in err and CLONE_FIX in err
    assert streamed.calls == []


def test_up_to_date_installs_nothing(world, streamed, capsys):
    assert run(streamed) == 0
    sha = git(world.checkout, "rev-parse", "--short", "HEAD")
    assert f"already up to date ({sha})" in capsys.readouterr().out
    assert streamed.calls == []


def test_new_commits_are_listed_pulled_installed_and_checked(world, streamed, capsys):
    world.push("add the thing")
    world.push("fix the other thing")
    assert run(streamed) == 0
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert "2 new commit(s):" in lines
    first = git(world.elsewhere, "log", "--format=%h", "-1", "HEAD~1")
    last = git(world.elsewhere, "log", "--format=%h", "-1", "HEAD")
    assert f"  {last} fix the other thing" in lines and f"  {first} add the thing" in lines
    assert git(world.checkout, "rev-parse", "HEAD") == git(world.elsewhere, "rev-parse", "HEAD")
    assert streamed.calls == [(["./server/install.sh"], world.checkout), ([PROGRAM, "doctor"], None)]
    headers = [line for line in lines if line.startswith("==> ")]
    assert len(headers) == 4  # one per step


def test_upgrade_exit_code_is_the_doctors(world, streamed):
    world.push("a change")
    streamed.exit_codes[PROGRAM] = 1
    assert run(streamed) == 1
    assert len(streamed.ran(PROGRAM)) == 1


def test_config_is_passed_to_doctor(world, streamed):
    world.push("a change")
    assert run(streamed, Path("/tmp/x.toml")) == 0
    assert streamed.ran(PROGRAM) == [([PROGRAM, "doctor", "--config", "/tmp/x.toml"], None)]


def test_modified_tracked_file_is_refused(world, streamed, capsys):
    world.push("a change")
    (world.checkout / "file.txt").write_text("edited on the VM")
    assert run(streamed) == 1
    assert "uncommitted changes" in capsys.readouterr().err
    assert streamed.calls == []


def test_untracked_files_do_not_block(world, streamed):
    world.push("a change")
    (world.checkout / "scratch.txt").write_text("not tracked")
    assert run(streamed) == 0
    assert len(streamed.ran("./server/install.sh")) == 1


def test_other_branch_is_refused(world, streamed, capsys):
    git(world.checkout, "switch", "--quiet", "-c", "work")
    assert run(streamed) == 1
    err = capsys.readouterr().err
    assert "'work'" in err and "main" in err
    assert streamed.calls == []


def test_diverged_checkout_is_refused_after_fetching(world, streamed, capsys):
    git(world.checkout, "config", "user.name", "Tester")
    commit(world.checkout, "local only")
    world.push("on origin")
    before = git(world.checkout, "rev-parse", "HEAD")
    assert run(streamed) == 1
    err = capsys.readouterr().err
    assert "diverged from origin/main" in err and "nothing was installed" in err
    assert git(world.checkout, "rev-parse", "HEAD") == before
    assert streamed.calls == []


def test_fetch_failure_is_reported(world, streamed, capsys):
    git(world.checkout, "remote", "set-url", "origin", str(world.origin) + "-gone")
    assert run(streamed) == 1
    assert "git fetch failed" in capsys.readouterr().err
    assert streamed.calls == []


def test_install_failure_skips_doctor(world, streamed, capsys):
    world.push("a change")
    streamed.exit_codes["./server/install.sh"] = 3
    assert run(streamed) == 1
    assert "install.sh failed (exit 3); see its output above" in capsys.readouterr().err
    assert streamed.ran(PROGRAM) == []


def test_empty_source_file_counts_as_missing(world, streamed, capsys):
    world.source_file.write_text("\n")
    assert run(streamed) == 1
    assert "don't know where the repo is" in capsys.readouterr().err
