import fcntl
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fixture import Fixture, build
from vitality_web.app import create_app


@pytest.fixture
def fx(tmp_path: Path) -> Fixture:
    return build(tmp_path / "fx")


@pytest.fixture
def app_dir(tmp_path: Path) -> Path:
    # A stand-in for the built app: index.html and one asset.
    folder = tmp_path / "dist"
    (folder / "assets").mkdir(parents=True)
    (folder / "index.html").write_text("<!doctype html><title>app</title>", encoding="utf-8")
    (folder / "assets" / "index-abc.js").write_text("console.log(1)", encoding="utf-8")
    return folder


@pytest.fixture
def client(fx: Fixture, app_dir: Path) -> TestClient:
    return TestClient(create_app(fx.model, fx.state, fx.captures, app_dir))


@pytest.fixture
def scheduler_running(fx: Fixture) -> Iterator[None]:
    """Hold the modeler's scheduler lock, as a running modeler does."""
    with open(fx.state / "scheduler.lock", "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield
