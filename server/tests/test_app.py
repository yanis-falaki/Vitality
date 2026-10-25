import errno
import logging
import socket
import threading
import time

import pytest
import uvicorn
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


# --- fix round 1 ---

MULTIPART = {"Content-Type": "multipart/form-data; boundary=x"}


class CountingIdentity(FakeIdentity):
    calls = 0
    async def who(self, ip):
        self.calls += 1
        return await super().who(ip)


def test_transfer_encoding_is_411_even_with_content_length(tmp_path):
    seen = {}
    app = create_app(FileStore(tmp_path), FakeIdentity("laptop"), ["laptop"])

    class Spy:  # outermost: records the headers the server really receives
        async def __call__(self, scope, receive, send):
            seen["headers"] = dict(scope["headers"])
            await app(scope, receive, send)

    r = TestClient(Spy()).post("/interactions", content=b"x" * 501,
                              headers={**MULTIPART, "Content-Length": "1", "Transfer-Encoding": "chunked"})
    assert b"content-length" in seen["headers"] and b"transfer-encoding" in seen["headers"]
    assert r.status_code == 411 and r.json()["ok"] is False


def test_content_length_that_lies_cannot_bypass_the_cap(tmp_path, monkeypatch):
    monkeypatch.setattr("vitality_server.app.MAX_UPLOAD_BYTES", 100)
    r = client(tmp_path).post("/interactions", content=b"x" * 501,
                              headers={**MULTIPART, "Content-Length": "1"})
    assert r.status_code == 413 and r.json() == {"ok": False, "error": "request larger than 100 bytes"}
    assert not (tmp_path / "captures").exists() or not any((tmp_path / "captures").iterdir())


@pytest.mark.parametrize("length", ["abc", "-5", "+5", "1.5", ""])
def test_malformed_content_length_is_413(tmp_path, length):
    r = client(tmp_path).post("/interactions", content=b"x", headers={**MULTIPART, "Content-Length": length})
    assert r.status_code == 413


def test_garbage_from_a_device_not_allowed_is_403_not_422(tmp_path):
    r = client(tmp_path, device="doer").post("/interactions", content=b"garbage", headers=MULTIPART)
    assert r.status_code == 403 and r.json() == {"ok": False, "error": "device not allowed"}


def test_oversize_is_413_before_identity_is_asked(tmp_path, monkeypatch):
    monkeypatch.setattr("vitality_server.app.MAX_UPLOAD_BYTES", 10)
    identity = CountingIdentity("doer")
    c = TestClient(create_app(FileStore(tmp_path), identity, ["laptop"]))
    assert post(c).status_code == 413 and identity.calls == 0


def test_identity_is_asked_once_per_upload(tmp_path):
    identity = CountingIdentity("laptop")
    c = TestClient(create_app(FileStore(tmp_path), identity, ["laptop"]))
    assert post(c).status_code == 201 and identity.calls == 1


def test_health_is_open_to_any_device(tmp_path):
    assert client(tmp_path, device="doer").get("/health").status_code == 200
    assert client(tmp_path, device=None).get("/health").status_code == 200


def test_unknown_route_is_error_reply(tmp_path):
    r = client(tmp_path).get("/nope")
    assert r.status_code == 404 and r.json()["ok"] is False


def test_disk_quota_is_507(tmp_path, monkeypatch):
    store = FileStore(tmp_path)
    monkeypatch.setattr(store, "put", lambda *a: (_ for _ in ()).throw(OSError(errno.EDQUOT, "Quota")))
    c = TestClient(create_app(store, FakeIdentity("laptop"), ["laptop"]))
    assert post(c).status_code == 507


@pytest.mark.parametrize("exc", [OSError(13, "Permission denied"), RuntimeError("boom")])
def test_unexpected_error_is_500_error_reply_and_logged(tmp_path, monkeypatch, caplog, exc):
    store = FileStore(tmp_path)
    monkeypatch.setattr(store, "put", lambda *a: (_ for _ in ()).throw(exc))
    c = TestClient(create_app(store, FakeIdentity("laptop"), ["laptop"]), raise_server_exceptions=False)
    r = post(c)
    assert r.status_code == 500 and r.json() == {"ok": False, "error": "internal server error"}
    assert any(rec.exc_info for rec in caplog.records)


def _raw_exchange(port: int, request: bytes) -> bytes:
    with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
        s.sendall(request)
        s.shutdown(socket.SHUT_WR)
        data = b""
        while chunk := s.recv(65536):
            data += chunk
    return data


def test_real_server_chunked_with_content_length_cannot_bypass_cap(tmp_path, monkeypatch):
    """The reviewer's repro: h11 frames by Transfer-Encoding when both headers are present."""
    monkeypatch.setattr("vitality_server.app.MAX_UPLOAD_BYTES", 100)
    app = create_app(FileStore(tmp_path), FakeIdentity("laptop"), ["laptop"])
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.time() + 5
        while not server.started and time.time() < deadline:
            time.sleep(0.01)
        port = server.servers[0].sockets[0].getsockname()[1]
        body = b"x" * 501
        head = (b"POST /interactions HTTP/1.1\r\nHost: x\r\nContent-Type: multipart/form-data; boundary=x\r\n"
                b"Content-Length: 1\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n")
        reply = _raw_exchange(port, head + b"%x\r\n" % len(body) + body + b"\r\n0\r\n\r\n")
        assert reply.startswith(b"HTTP/1.1 411"), reply[:80]
    finally:
        server.should_exit = True
        thread.join(5)
