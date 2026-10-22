import asyncio
import json
import logging
import socket
import stat
from unittest.mock import AsyncMock

import pytest

from vitality_client.control import (
    ControlServer, DaemonNotRunning, State, StateFile, parse_duration, send_command,
)


@pytest.mark.parametrize("text,secs", [("30m", 1800.0), ("2h", 7200.0), ("45s", 45.0)])
def test_parse_duration(text, secs): assert parse_duration(text) == secs


@pytest.mark.parametrize("bad", ["", "30", "m", "-5m", "1.5h", "3d"])
def test_parse_duration_rejects(bad):
    with pytest.raises(ValueError, match="invalid duration"): parse_duration(bad)


def test_state_file_roundtrip_and_defaults(tmp_path):
    f = StateFile(tmp_path / "s/state.json"); assert f.load() == State()
    f.save(State(True, 123.0, "tok")); assert f.load() == State(True, 123.0, "tok")
    (tmp_path / "s/state.json").write_text("{not json"); assert f.load() == State(paused=True)


def test_state_file_ignores_unknown_keys(tmp_path):
    p = tmp_path / "state.json"
    p.write_text(json.dumps({"paused": True, "future": 1}))
    assert StateFile(p).load() == State(paused=True)


@pytest.mark.parametrize("content", [
    {"paused": "yes"}, {"paused_until": "soon"}, {"restore_token": 5}, {"paused": 1},
    [1, 2], "str",
])
def test_state_file_wrong_types_are_corrupt(tmp_path, caplog, content):
    p = tmp_path / "state.json"
    p.write_text(json.dumps(content))
    with caplog.at_level(logging.WARNING):
        assert StateFile(p).load() == State(paused=True)
    assert any(r.levelno == logging.WARNING for r in caplog.records)


def test_state_file_corrupt_log_has_no_token(tmp_path, caplog):
    p = tmp_path / "state.json"
    p.write_text(json.dumps({"restore_token": "SECRET-TOKEN", "paused": "bad"}))
    with caplog.at_level(logging.WARNING):
        StateFile(p).load()
    assert "SECRET-TOKEN" not in caplog.text


def test_state_file_save_leaves_no_temp_files(tmp_path):
    f = StateFile(tmp_path / "s/state.json"); f.save(State(restore_token="t"))
    assert [p.name for p in (tmp_path / "s").iterdir()] == ["state.json"]


async def test_request_response_roundtrip(tmp_path):
    async def handler(req): return {"ok": True, "echo": req["cmd"]}
    srv = ControlServer(tmp_path / "c.sock", handler); await srv.start()
    assert await send_command(tmp_path / "c.sock", {"cmd": "status"}) == {"ok": True, "echo": "status"}
    await srv.close()


async def test_stale_socket_is_replaced(tmp_path):                       # Review Focus 4
    stale = tmp_path / "c.sock"; s = socket.socket(socket.AF_UNIX); s.bind(str(stale)); s.close()
    srv = ControlServer(stale, AsyncMock(return_value={"ok": True})); await srv.start()
    assert (await send_command(stale, {"cmd": "status"}))["ok"]; await srv.close()


async def test_second_server_refuses(tmp_path):
    a = ControlServer(tmp_path / "c.sock", AsyncMock(return_value={"ok": True})); await a.start()
    with pytest.raises(RuntimeError, match="already running"):
        await ControlServer(tmp_path / "c.sock", AsyncMock()).start()
    await a.close()


async def test_daemon_not_running(tmp_path):                             # Review Focus 4
    with pytest.raises(DaemonNotRunning): await send_command(tmp_path / "missing.sock", {"cmd": "status"})


async def test_handler_error_is_reported(tmp_path):
    srv = ControlServer(tmp_path / "c.sock", AsyncMock(side_effect=ValueError("boom"))); await srv.start()
    assert await send_command(tmp_path / "c.sock", {"cmd": "x"}) == {"ok": False, "error": "boom"}
    await srv.close()


async def test_non_socket_path_is_daemon_not_running(tmp_path):
    p = tmp_path / "c.sock"; p.write_text("x")
    with pytest.raises(DaemonNotRunning, match="daemon is not running"):
        await send_command(p, {"cmd": "status"})


async def test_malformed_json_gets_invalid_request(tmp_path):
    handler = AsyncMock(return_value={"ok": True})
    srv = ControlServer(tmp_path / "c.sock", handler); await srv.start()
    r, w = await asyncio.open_unix_connection(str(tmp_path / "c.sock"))
    w.write(b"{nope\n"); await w.drain()
    assert json.loads(await r.readline()) == {"ok": False, "error": "invalid request"}
    w.close(); await w.wait_closed(); handler.assert_not_called(); await srv.close()


async def test_non_object_json_gets_invalid_request(tmp_path):
    srv = ControlServer(tmp_path / "c.sock", AsyncMock(return_value={"ok": True})); await srv.start()
    r, w = await asyncio.open_unix_connection(str(tmp_path / "c.sock"))
    w.write(b"[1]\n"); await w.drain()
    assert json.loads(await r.readline()) == {"ok": False, "error": "invalid request"}
    w.close(); await w.wait_closed(); await srv.close()


async def test_permissions_and_close_removes_socket(tmp_path):
    sock = tmp_path / "run/vitality/c.sock"
    srv = ControlServer(sock, AsyncMock(return_value={"ok": True})); await srv.start()
    assert stat.S_IMODE(sock.stat().st_mode) == 0o600
    assert stat.S_IMODE(sock.parent.stat().st_mode) == 0o700
    await srv.close()
    assert not sock.exists()


async def test_response_timeout_is_daemon_not_running(tmp_path, monkeypatch):
    import vitality_client.control.channel as control
    monkeypatch.setattr(control, "RESPONSE_TIMEOUT", 0.05)
    async def slow(req): await asyncio.sleep(1); return {"ok": True}
    srv = ControlServer(tmp_path / "c.sock", slow); await srv.start()
    with pytest.raises(DaemonNotRunning):
        await send_command(tmp_path / "c.sock", {"cmd": "status"})
    await srv.close()


def test_state_file_unreadable_loads_paused(tmp_path, caplog):
    p = tmp_path / "state.json"; p.mkdir()  # exists but cannot be read as a file
    with caplog.at_level(logging.WARNING):
        assert StateFile(p).load() == State(paused=True)
    assert any(r.levelno == logging.WARNING for r in caplog.records)


async def test_close_does_not_hang_on_silent_client(tmp_path):
    srv = ControlServer(tmp_path / "c.sock", AsyncMock(return_value={"ok": True})); await srv.start()
    r, w = await asyncio.open_unix_connection(str(tmp_path / "c.sock"))
    await asyncio.sleep(0.05)  # let the server accept and block reading
    await asyncio.wait_for(srv.close(), timeout=1)
    assert not (tmp_path / "c.sock").exists()
    w.close()


async def test_silent_client_is_dropped_after_request_timeout(tmp_path, monkeypatch):
    import vitality_client.control.channel as control
    monkeypatch.setattr(control, "REQUEST_TIMEOUT", 0.05)
    handler = AsyncMock(return_value={"ok": True})
    srv = ControlServer(tmp_path / "c.sock", handler); await srv.start()
    r, w = await asyncio.open_unix_connection(str(tmp_path / "c.sock"))
    assert await asyncio.wait_for(r.read(), timeout=1) == b""  # closed, no response
    handler.assert_not_called()
    w.close(); await w.wait_closed(); await srv.close()
