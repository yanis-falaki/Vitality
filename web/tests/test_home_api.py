"""The endpoints behind Home: the modeler's commits and what they changed, and how far the evidence has come."""

import subprocess
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fixture import Fixture
from vitality_web.app import create_app
from vitality_web.changes import ChangeLog


def test_changes_lists_the_modeler_commits_newest_first(client: TestClient) -> None:
    reply = client.get("/api/changes").json()
    assert reply["more"] is False
    commits = reply["commits"]
    # The evidence commit and the owner's own commit are not the modeler's.
    assert [c["session"] for c in commits] == ["2026-11-05T03-46-00Z", "2026-11-04T03-00-00Z", "2026-11-03T04-00-00Z"]
    assert commits[0]["time"] == "2026-11-04T22:52:02-05:00"
    assert [(c["actions_added"], c["actions_changed"]) for c in commits] == [(8, 0), (11, 1), (9, 0)]


def test_change_items_come_from_the_frontmatter_before_and_after(client: TestClient) -> None:
    commits = client.get("/api/changes").json()["commits"]
    items = lambda c: [(i["kind"], i["id"], i["before"], i["after"], i["other"]) for i in c["items"]]  # noqa: E731
    assert items(commits[2]) == [("created", "str-0001", None, None, None)] + [
        ("created", f"act-000{n}", None, None, None) for n in range(1, 7)]
    assert items(commits[1]) == [
        ("created", "str-0002", None, None, None),
        ("retitled", "act-0001", "Building a key-value store", "Building a toy key-value store in Rust", None),
        ("status", "act-0001", "tentative", "established", None),
        ("confidence", "act-0001", "6", "7", None),
        ("status", "act-0003", "tentative", "established", None),
        ("confidence", "act-0003", "5", "7", None),
        ("serves", "act-0003", None, None, "str-0002"),
        ("removed", "act-0006", None, None, "act-0001"),   # its action moved to act-0001: merged
    ]
    assert items(commits[0]) == [
        ("confidence", "str-0001", "5", "7", None), ("confidence", "str-0002", "4", "5", None),
        ("confidence", "act-0001", "7", "8", None), ("confidence", "act-0002", "4", "5", None),
        ("serves", "act-0002", None, None, "str-0002"),
    ]
    removed = commits[1]["items"][-1]
    assert (removed["level"], removed["title"]) == ("activity", "Reading the Rust book")  # its title before


def test_changes_are_paged_by_commit(client: TestClient) -> None:
    first = client.get("/api/changes?limit=2").json()
    assert len(first["commits"]) == 2 and first["more"] is True
    rest = client.get(f"/api/changes?limit=2&before={first['commits'][-1]['commit'][:12]}").json()
    assert [c["session"] for c in rest["commits"]] == ["2026-11-03T04-00-00Z"] and rest["more"] is False


@pytest.mark.parametrize(("query", "status"), [("before=0000000", 404), ("before=--all", 422), ("limit=0", 422)])
def test_changes_refuses_unknown_commits_and_bad_queries(client: TestClient, query: str, status: int) -> None:
    assert client.get(f"/api/changes?{query}").status_code == status


def test_a_commit_is_read_once(fx: Fixture, monkeypatch: pytest.MonkeyPatch) -> None:
    # A commit never changes: what was read of it is kept by its hash; only the log is listed again.
    log = ChangeLog(fx.model)
    first = log.page(10)
    calls: list[str] = []
    real = subprocess.run

    def spy(args: list[str], **kwargs: object) -> object:
        calls.append(args[3])
        return real(args, **kwargs)  # type: ignore[call-overload]

    monkeypatch.setattr(subprocess, "run", spy)
    assert log.page(10) == first
    assert calls == ["log"]


def test_changes_of_a_model_that_is_not_a_repo(tmp_path: Path, app_dir: Path) -> None:
    client = TestClient(create_app(tmp_path / "model", tmp_path / "state", tmp_path / "captures", app_dir))
    assert client.get("/api/changes").json() == {"commits": [], "more": False}


def test_pipeline_gives_the_newest_capture_and_observation(fx: Fixture, app_dir: Path) -> None:
    now = datetime.fromisoformat("2026-11-04T23:00:00-05:00")
    client = TestClient(create_app(fx.model, fx.state, fx.captures, app_dir, now=now))
    # The newest folders are the waiting interactions (22:44 local); links and other files are ignored.
    assert client.get("/api/pipeline").json() == {
        "now": "2026-11-04T23:00:00-05:00", "last_capture": "2026-11-05T03:44:00Z",
        "last_observation": "2026-11-05T03:44:00Z"}
    (fx.captures / "laptop" / "2026-11-05" / "04-10-00").mkdir(parents=True)
    assert client.get("/api/pipeline").json()["last_capture"] == "2026-11-05T04:10:00Z"


def test_pipeline_without_evidence_reads_the_clock(tmp_path: Path, app_dir: Path) -> None:
    client = TestClient(create_app(tmp_path / "model", tmp_path / "state", tmp_path / "captures", app_dir))
    reply = client.get("/api/pipeline").json()
    assert reply["last_capture"] is None and reply["last_observation"] is None
    assert abs((datetime.fromisoformat(reply["now"]) - datetime.now().astimezone()).total_seconds()) < 60
