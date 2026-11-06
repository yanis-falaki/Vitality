import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fixture import Fixture
from vitality_web.app import create_app
from vitality_web.main import main, web_port


def test_model_lists_every_level_by_start(client: TestClient, fx: Fixture) -> None:
    model = client.get("/api/model").json()
    assert [s["id"] for s in model["strivings"]] == ["str-0001", "str-0002"]
    assert [a["id"] for a in model["activities"]] == ["act-0001", "act-0002", "act-0003", "act-0004", "act-0005"]
    assert [a["id"] for a in model["actions"]] == fx.actions
    first = model["actions"][0]
    assert first["start"] == "2026-11-02T09:04:00-05:00"  # local time with its offset, as in the file
    assert first["body"].startswith("Set up a new Rust crate")
    assert model["strivings"][0]["relations"]["conflicts_with"] == ["str-0002"]


def test_model_summarizes_the_cited_interactions(client: TestClient, fx: Fixture) -> None:
    interactions = client.get("/api/model").json()["interactions"]
    assert sorted(interactions) == sorted(set(fx.interactions) - set(fx.waiting))
    shared = interactions["laptop/2026-11-02/14-18-00"]  # holds an operation of the next action too
    assert shared == {"start": "2026-11-02T09:18:00-05:00", "end": "2026-11-02T09:30:00-05:00",
                      "operations": 3, "screenshots": 3,
                      "first_operation": "Opened doc.rust-lang.org on the collections chapter"}


def test_malformed_files_are_listed_not_raised(client: TestClient, fx: Fixture) -> None:
    (fx.model / "operations" / "laptop" / "2026-11-02" / "14-04-00.md").unlink()
    (fx.model / "strivings" / "str-0009.md").write_text("no frontmatter", encoding="utf-8")
    reply = client.get("/api/model")
    assert reply.status_code == 200
    problems = {p["path"]: p["error"] for p in reply.json()["problems"]}
    assert problems == {
        "activities/act-0006.md": "confidence: Input should be a valid integer, unable to parse string as an integer",
        "strivings/str-0009.md": "missing frontmatter",
        "operations/laptop/2026-11-02/14-04-00.md": "missing, but cited by an action",
    }
    assert "laptop/2026-11-02/14-04-00" not in reply.json()["interactions"]


def test_model_follows_changes_to_the_files(client: TestClient, fx: Fixture) -> None:
    assert len(client.get("/api/model").json()["strivings"]) == 2
    shutil.copy(fx.model / "strivings" / "str-0002.md", fx.model / "strivings" / "str-0003.md")
    text = (fx.model / "strivings" / "str-0003.md").read_text().replace("str-0002", "str-0003")
    (fx.model / "strivings" / "str-0003.md").write_text(text)
    assert [s["id"] for s in client.get("/api/model").json()["strivings"]] == ["str-0001", "str-0002", "str-0003"]


def test_evidence_of_an_interaction(client: TestClient) -> None:
    reply = client.get("/api/evidence/laptop/2026-11-02/14-18-00")
    assert reply.status_code == 200
    evidence = reply.json()
    assert [op["id"] for op in evidence["operations"]] == ["op1", "op2", "op3"]
    assert evidence["screenshots"] == ["0000.png", "0240.png", "0480.png"]
    assert evidence["actions"] == ["2026-11-02/14-18-00", "2026-11-02/14-29-00"]
    assert evidence["observation"]["body"].startswith("The screen shows one window")
    assert evidence["start"] == "2026-11-02T09:18:00-05:00"


def test_evidence_without_an_observation(client: TestClient, fx: Fixture) -> None:
    (fx.model / "observations" / "laptop" / "2026-11-02" / "14-18-00.md").unlink()
    assert client.get("/api/evidence/laptop/2026-11-02/14-18-00").json()["observation"] is None


def test_waiting_evidence_is_cited_by_no_action(client: TestClient, fx: Fixture) -> None:
    assert client.get(f"/api/evidence/{fx.waiting[0]}").json()["actions"] == []


@pytest.mark.parametrize("name", ["laptop/2026-01-01/00-00-00", "nonsense", "laptop/../../etc/passwd",
                                  "laptop/2026-11-02/14-18-00.md"])
def test_unknown_evidence_is_404(client: TestClient, name: str) -> None:
    assert client.get(f"/api/evidence/{name}").status_code == 404


def test_modeler_not_running(client: TestClient) -> None:
    modeler = client.get("/api/modeler").json()
    assert modeler["status"] == {"state": "not running", "since": None, "until": None}
    assert modeler["waiting"] == 3  # two untracked operations files: 2 + 1 operations
    assert [s["run"] for s in modeler["sessions"]] == [
        "2026-11-03T04-00-00Z", "2026-11-04T03-00-00Z", "2026-11-05T04-20-00Z"]  # the torn line skipped
    assert "offered" not in modeler["sessions"][0]
    assert modeler["sessions"][0]["changes"]["actions"] == 9
    assert modeler["sessions"][0]["trigger"] == "sitting"  # other fields are kept


@pytest.mark.usefixtures("scheduler_running")
def test_modeler_running_shows_its_status(client: TestClient) -> None:
    assert client.get("/api/modeler").json()["status"]["state"] == "idle"


def test_modeler_without_records_or_repo(tmp_path: Path, app_dir: Path) -> None:
    client = TestClient(create_app(tmp_path / "model", tmp_path / "state", tmp_path / "captures", app_dir))
    assert client.get("/api/modeler").json() == {"status": {"state": "not running", "since": None, "until": None},
                                                 "waiting": None, "sessions": []}
    assert client.get("/api/model").json() == {"strivings": [], "activities": [], "actions": [],
                                               "interactions": {}, "problems": []}


def test_screenshot_is_served_with_long_cache(client: TestClient) -> None:
    reply = client.get("/captures/laptop/2026-11-02/14-18-00/0000.png")
    assert reply.status_code == 200
    assert reply.headers["content-type"] == "image/png"
    assert "max-age=31536000" in reply.headers["cache-control"]
    assert reply.content.startswith(b"\x89PNG")


@pytest.mark.parametrize("path", [
    "laptop/escape.png",                          # a link to a file outside the captures
    "laptop/outside/secret.png",                  # through a linked folder outside them
    "laptop/2026-11-02/14-18-00/interaction.json",  # not an image
    "laptop/2026-11-02/14-18-00/9999.png",
    "%2e%2e/secret.png",
    "laptop/2026-11-02",
])
def test_screenshot_outside_or_unknown_is_404(client: TestClient, path: str) -> None:
    assert client.get(f"/captures/{path}").status_code == 404


def test_resolve_refuses_parent_steps(fx: Fixture) -> None:
    # (An HTTP client folds `..` away before sending, so this is checked on the function.)
    from vitality_web.captures import resolve
    assert resolve(fx.captures, "laptop/2026-11-02/14-18-00/0000.png") is not None
    assert resolve(fx.captures, "laptop/2026-11-02/14-18-00/../14-18-00/0000.png") is None
    assert resolve(fx.captures, "../secret.png") is None
    assert resolve(fx.captures, str(fx.captures / "laptop/2026-11-02/14-18-00/0000.png")) is None


def test_screenshots_listing_skips_escaping_links(fx: Fixture) -> None:
    from vitality_web.captures import screenshots
    folder = fx.captures / "laptop" / "2026-11-02" / "14-18-00"
    (folder / "0999.png").symlink_to(fx.captures.parent / "secret.png")
    assert screenshots(fx.captures, "laptop/2026-11-02/14-18-00") == ["0000.png", "0240.png", "0480.png"]
    assert screenshots(fx.captures, "laptop/outside") == []


def test_app_routes_fall_back_to_index(client: TestClient) -> None:
    for path in ("/", "/graph", "/day/2026-11-03", "/cards?sel=act-0001"):
        reply = client.get(path)
        assert reply.status_code == 200 and "<title>app</title>" in reply.text
        assert reply.headers["cache-control"] == "no-cache"
    asset = client.get("/assets/index-abc.js")
    assert asset.text == "console.log(1)" and "immutable" in asset.headers["cache-control"]


def test_unknown_api_path_is_404_not_the_app(client: TestClient) -> None:
    assert client.get("/api/nothing").status_code == 404


def test_health(client: TestClient) -> None:
    assert client.get("/api/health").json() == {"ok": True}


def test_nothing_is_written(client: TestClient, fx: Fixture) -> None:
    before = subprocess.run(["git", "-C", str(fx.model), "status", "--porcelain"], capture_output=True, text=True).stdout
    for path in ("/api/model", "/api/modeler", f"/api/evidence/{fx.interactions[0]}"):
        client.get(path)
    after = subprocess.run(["git", "-C", str(fx.model), "status", "--porcelain"], capture_output=True, text=True).stdout
    assert before == after
    assert sorted(p.name for p in fx.state.iterdir()) == ["sessions.jsonl", "status.json"]


def test_committed_openapi_schema_is_current(capsys: pytest.CaptureFixture[str]) -> None:
    # The app's types are generated from app/openapi.json; regenerate both when the API changes (README.md).
    assert main(["openapi"]) == 0
    committed = (Path(__file__).parent.parent / "app" / "openapi.json").read_text(encoding="utf-8")
    assert json.loads(capsys.readouterr().out) == json.loads(committed)


def test_web_port() -> None:
    assert web_port({}) == 4000
    assert web_port({"WEB_PORT": "4100"}) == 4100
    with pytest.raises(ValueError, match="WEB_PORT must be an integer"):
        web_port({"WEB_PORT": "abc"})
    assert main(["serve"], {"WEB_PORT": "0"}) == 2
