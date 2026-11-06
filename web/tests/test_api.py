import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pydantic import BaseModel

from fixture import Fixture
from vitality_usermodel.formats import Kind
from vitality_web.app import create_app
from vitality_web.model import ModelReader
from vitality_web.main import main, web_port


def test_model_lists_every_level_by_start(client: TestClient, fx: Fixture) -> None:
    model = client.get("/api/model").json()
    assert [s["id"] for s in model["strivings"]] == ["str-0001", "str-0002"]
    assert [a["id"] for a in model["activities"]] == ["act-0001", "act-0002", "act-0003", "act-0004", "act-0005"]
    assert [a["id"] for a in model["actions"]] == fx.actions
    first = model["actions"][0]
    assert first["start"] == "2026-11-02T09:04:00-05:00"  # local time with its offset, as in the file
    assert "body" not in first and "operations" not in first  # those come with the action (/api/actions)
    assert first["interactions"] == ["laptop/2026-11-02/14-04-00", "laptop/2026-11-02/14-10-00"]
    assert model["strivings"][0]["relations"]["conflicts_with"] == ["str-0002"]
    assert model["strivings"][0]["body"].startswith("Most mornings begin in a terminal")


def test_action_with_its_operations_and_body(client: TestClient) -> None:
    action = client.get("/api/actions/2026-11-02/14-29-00").json()
    assert action["body"].startswith("Implemented get and put")
    assert action["operations"] == ["laptop/2026-11-02/14-18-00#op3", "laptop/2026-11-02/14-29-00#op1",
                                    "laptop/2026-11-02/14-36-00#op1"]
    assert action["interactions"] == ["laptop/2026-11-02/14-18-00", "laptop/2026-11-02/14-29-00",
                                      "laptop/2026-11-02/14-36-00"]


@pytest.mark.parametrize("id", ["2026-01-01/00-00-00", "nonsense", "../strivings/str-0001"])
def test_unknown_action_is_404(client: TestClient, id: str) -> None:
    assert client.get(f"/api/actions/{id}").status_code == 404


def test_interactions_summarizes_the_cited_ones(client: TestClient, fx: Fixture) -> None:
    interactions = client.get("/api/interactions").json()
    assert sorted(interactions) == sorted(set(fx.interactions) - set(fx.waiting))
    shared = interactions["laptop/2026-11-02/14-18-00"]  # holds an operation of the next action too
    assert shared == {"start": "2026-11-02T09:18:00-05:00", "end": "2026-11-02T09:30:00-05:00",
                      "operations": 3, "screenshots": 3,
                      "first_operation": "Opened doc.rust-lang.org on the collections chapter"}


def test_interactions_of_some_days(client: TestClient) -> None:
    # By the local day each started on, both ends included.
    one = client.get("/api/interactions?from=2026-11-03&to=2026-11-03").json()
    assert one and all(s["start"].startswith("2026-11-03") for s in one.values())
    since = client.get("/api/interactions?from=2026-11-03").json()
    assert {s["start"][:10] for s in since.values()} == {"2026-11-03", "2026-11-04"}
    assert client.get("/api/interactions?to=2026-11-01").json() == {}
    assert client.get("/api/interactions?from=yesterday").status_code == 422


def test_model_is_sent_gzipped_with_an_etag(client: TestClient) -> None:
    reply = client.get("/api/model", headers={"Accept-Encoding": "gzip"})
    assert reply.headers["content-encoding"] == "gzip" and reply.headers["cache-control"] == "no-cache"
    again = client.get("/api/model", headers={"If-None-Match": reply.headers["etag"]})
    assert again.status_code == 304 and again.content == b""
    plain = client.get("/api/model", headers={"Accept-Encoding": "identity"})
    assert "content-encoding" not in plain.headers and plain.json() == reply.json()


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
    assert "laptop/2026-11-02/14-04-00" not in client.get("/api/interactions").json()


def test_model_follows_changes_to_the_files(client: TestClient, fx: Fixture) -> None:
    assert len(client.get("/api/model").json()["strivings"]) == 2
    shutil.copy(fx.model / "strivings" / "str-0002.md", fx.model / "strivings" / "str-0003.md")
    text = (fx.model / "strivings" / "str-0003.md").read_text().replace("str-0002", "str-0003")
    (fx.model / "strivings" / "str-0003.md").write_text(text)
    assert [s["id"] for s in client.get("/api/model").json()["strivings"]] == ["str-0001", "str-0002", "str-0003"]


def test_unchanged_files_are_parsed_once(client: TestClient, fx: Fixture, monkeypatch: pytest.MonkeyPatch) -> None:
    import vitality_web.model
    parsed: list[Path] = []
    real = vitality_web.model.read_file

    def counting(path: Path, kind: Kind) -> tuple[BaseModel, str]:
        parsed.append(path)
        return real(path, kind)

    monkeypatch.setattr(vitality_web.model, "read_file", counting)
    client.get("/api/model")
    assert len(parsed) > 50  # every level file and every cited operations file, once
    parsed.clear()
    client.get("/api/model")
    client.get("/api/interactions")
    client.get(f"/api/actions/{fx.actions[0]}")
    assert parsed == []
    action = fx.model / "actions" / "2026-11-02" / "14-04-00.md"
    action.write_text(action.read_text().replace("Set up a new Rust crate", "Set up a new crate"))
    model = client.get("/api/model").json()
    assert parsed == [action]
    assert model["actions"][0]["summary"].startswith("Set up a new crate for")


def test_summaries_follow_changes_to_operations_and_captures(client: TestClient, fx: Fixture) -> None:
    name = "laptop/2026-11-02/14-18-00"
    assert client.get("/api/interactions").json()[name]["screenshots"] == 3
    folder = fx.captures / name
    (folder / "0600.png").write_bytes((folder / "0000.png").read_bytes())
    ops = fx.model / "operations" / f"{name}.md"
    ops.write_text(ops.read_text().replace("Opened doc.rust-lang.org", "Opened the Rust book"))
    summary = client.get("/api/interactions").json()[name]
    assert summary["screenshots"] == 4
    assert summary["first_operation"].startswith("Opened the Rust book")


def test_a_fresh_check_is_reused(fx: Fixture) -> None:
    # Within `fresh` seconds the files are not looked at again: a view's burst of requests shares one check.
    reader = ModelReader(fx.model, fx.captures, fresh=60)
    first = reader.snapshot()
    shutil.copy(fx.model / "strivings" / "str-0002.md", fx.model / "strivings" / "str-0003.md")
    assert reader.snapshot() is first
    reader.fresh = 0
    assert len(reader.snapshot().model.strivings) == 3


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
    assert client.get("/api/model").json() == {"strivings": [], "activities": [], "actions": [], "problems": []}
    assert client.get("/api/interactions").json() == {}


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
    for path in ("/api/model", "/api/modeler", f"/api/evidence/{fx.interactions[0]}", "/api/interactions",
                 f"/api/actions/{fx.actions[0]}"):
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
