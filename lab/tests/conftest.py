import subprocess
from pathlib import Path

import pytest


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A git repo with one commit holding `observer/core.py`."""
    root = tmp_path / "repo"
    (root / "observer").mkdir(parents=True)
    (root / "observer" / "core.py").write_text("x = 1\n")
    git(root, "init", "-q")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "T")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "first")
    return root
