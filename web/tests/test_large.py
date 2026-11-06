"""The API at scale: about four months of a fictional model (`fixture_large`), its sizes and times.

The fixture is built once for these tests (about 15 seconds). The bounds are generous, for a slow
machine; the numbers measured on the hub are in the web spec, section 10.
"""

import time

import pytest
from fastapi.testclient import TestClient

from fixture import Fixture
from fixture_large import build_large
from vitality_web.app import create_app


@pytest.fixture(scope="module")
def large(tmp_path_factory: pytest.TempPathFactory) -> Fixture:
    return build_large(tmp_path_factory.mktemp("large") / "fx")


@pytest.fixture(scope="module")
def client(large: Fixture) -> TestClient:
    client = TestClient(create_app(large.model, large.state, large.captures, None, fresh=0))
    client.get("/api/model")  # the first read parses every file
    return client


def timed(client: TestClient, path: str, **headers: str) -> tuple[float, int, bytes]:
    # Seconds, status and the body as sent (before decompression).
    start = time.perf_counter()
    with client.stream("GET", path, headers=headers) as reply:
        body = b"".join(reply.iter_raw())
    return time.perf_counter() - start, reply.status_code, body


def test_the_fixture_is_four_months_of_model(large: Fixture) -> None:
    days = {a[:10] for a in large.actions}
    assert 80 <= len(days) <= 110 and len(large.actions) / len(days) > 15
    assert len(large.interactions) > 10_000


def test_model_is_small_on_the_wire_and_quick(client: TestClient, large: Fixture) -> None:
    seconds, status, body = timed(client, "/api/model", **{"Accept-Encoding": "gzip"})
    assert status == 200 and len(body) < 250_000  # gzipped: no bodies, interactions only by name
    assert seconds < 1.0
    model = client.get("/api/model").json()
    assert len(model["actions"]) == len(large.actions) and model["problems"] == []


def test_an_unchanged_model_is_a_304(client: TestClient) -> None:
    etag = client.get("/api/model").headers["etag"]
    seconds, status, body = timed(client, "/api/model", **{"If-None-Match": etag})
    assert status == 304 and body == b"" and seconds < 1.0


def test_a_month_of_interactions_is_quick(client: TestClient) -> None:
    seconds, status, body = timed(client, "/api/interactions?from=2026-10-06", **{"Accept-Encoding": "gzip"})
    assert status == 200 and seconds < 1.0 and len(body) < 250_000
    assert 2_000 < len(client.get("/api/interactions?from=2026-10-06").json()) < 6_000


def test_one_action_and_one_evidence_are_quick(client: TestClient, large: Fixture) -> None:
    seconds, status, _ = timed(client, f"/api/actions/{large.actions[-1]}")
    assert status == 200 and seconds < 0.5
    seconds, status, _ = timed(client, f"/api/evidence/{large.interactions[-3]}")
    assert status == 200 and seconds < 0.5


def test_a_change_is_seen_without_reading_everything_again(client: TestClient, large: Fixture) -> None:
    path = next((large.model / "activities").glob("*.md"))
    path.write_text(path.read_text().replace("title: ", "title: Renamed ", 1))
    seconds, _, _ = timed(client, "/api/model")
    assert seconds < 2.0
    assert any(a["title"].startswith("Renamed ") for a in client.get("/api/model").json()["activities"])

