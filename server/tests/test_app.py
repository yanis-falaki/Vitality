import logging

import pytest
from fastapi.testclient import TestClient
from test_storage import META, WEBP  # reuse fixtures
from vitality_server.app import create_app
from vitality_server.storage import FileStore


class FakeIdentity:
    def __init__(self, name: str | None): self.name = name
    async def who(self, ip): return self.name


def client(tmp_path, device: str | None = "laptop"):
    return TestClient(create_app(FileStore(tmp_path), FakeIdentity(device), ["laptop"]))


def post(c, meta=META, files=(("0000.webp", WEBP),)):
    return c.post("/interactions", data={"interaction": meta.model_dump_json()},
                  files=[("shots", (n, b, "image/webp")) for n, b in files])


def test_new_then_existing(tmp_path):
    c = client(tmp_path)
    r = post(c); assert r.status_code == 201 and r.json() == {"ok": True, "stored": "new"}
    r = post(c); assert r.status_code == 200 and r.json()["stored"] == "existing"
    assert (tmp_path / "captures/laptop/2026-10-25/17-00-00/0000.webp").exists()


def test_device_not_allowed(tmp_path):
    r = post(client(tmp_path, device="doer")); assert r.status_code == 403
    assert r.json() == {"ok": False, "error": "device not allowed"}


def test_unknown_device(tmp_path):
    assert post(client(tmp_path, device=None)).status_code == 403


def test_invalid_json_is_422_error_reply(tmp_path):
    r = client(tmp_path).post("/interactions", data={"interaction": "{}"},
                              files=[("shots", ("0000.webp", WEBP, "image/webp"))])
    assert r.status_code == 422 and r.json()["ok"] is False
    assert set(r.json()) == {"ok", "error"} and isinstance(r.json()["error"], str)


def test_missing_shots_is_422_error_reply(tmp_path):
    r = client(tmp_path).post("/interactions", data={"interaction": META.model_dump_json()})
    assert r.status_code == 422 and r.json()["ok"] is False


def test_storage_rejection_is_422(tmp_path):
    r = post(client(tmp_path), files=(("0001.webp", WEBP),))
    assert r.status_code == 422 and "files" in r.json()["error"]


def test_conflict_is_409(tmp_path):
    c = client(tmp_path); post(c)
    r = post(c, META.model_copy(update={"tz": "UTC"}))
    assert r.status_code == 409 and r.json()["ok"] is False


def test_too_large_is_413(tmp_path, monkeypatch):
    monkeypatch.setattr("vitality_server.app.MAX_UPLOAD_BYTES", 10)
    r = post(client(tmp_path))
    assert r.status_code == 413 and r.json()["ok"] is False


def test_missing_content_length_is_411(tmp_path):
    def body():
        yield b"x"
    r = client(tmp_path).post("/interactions", content=body())  # chunked: no Content-Length
    assert r.status_code == 411 and r.json()["ok"] is False


def test_disk_full_is_507(tmp_path, monkeypatch):
    store = FileStore(tmp_path)
    monkeypatch.setattr(store, "put", lambda *a: (_ for _ in ()).throw(OSError(28, "No space left")))
    c = TestClient(create_app(store, FakeIdentity("laptop"), ["laptop"]))
    r = post(c)
    assert r.status_code == 507 and r.json()["ok"] is False


def test_other_os_error_is_not_swallowed(tmp_path, monkeypatch):
    store = FileStore(tmp_path)
    monkeypatch.setattr(store, "put", lambda *a: (_ for _ in ()).throw(OSError(13, "Permission denied")))
    c = TestClient(create_app(store, FakeIdentity("laptop"), ["laptop"]), raise_server_exceptions=False)
    assert post(c).status_code == 500


def test_health(tmp_path):
    r = client(tmp_path).get("/health")
    assert r.status_code == 200 and r.json()["ok"] is True and r.json()["disk_free_bytes"] > 0


def test_logs_upload_and_rejection(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    c = client(tmp_path); post(c)
    assert any("laptop" in m and META.name in m and "new" in m for m in caplog.messages)
    caplog.clear()
    post(client(tmp_path, device="doer"))
    assert any("doer" in m and "not allowed" in m for m in caplog.messages)
    assert not any(WEBP.decode("latin-1") in m for m in caplog.messages)
