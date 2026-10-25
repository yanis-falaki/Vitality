import json
from typing import cast

import pytest

from vitality_client.cli import main
from vitality_client.cli.control import format_status
from vitality_client.shared.channel import Reply, Request, Status

STATUS: Status = {
    "ok": True, "state": "idle", "paused_until": None, "captures_today": 412,
    "last_capture": "2026-10-21T14:02:05.312Z", "disk_bytes": 3_100_000_000, "disk_warning": False,
    "activity": "available", "capture": "available",
    "paths": {"config": "/c", "data": "/d", "state": "/s"},
    "upload": {"state": "disabled", "pending": 0, "last_upload": None},
}


def status(**changes) -> Status:
    """A daemon status reply: STATUS with some fields changed."""
    return cast(Status, {**STATUS, **changes})


def test_status_when_daemon_not_running(tmp_path, monkeypatch, capsys):      # Review Focus 4
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    assert main(["status"]) == 1
    assert "vitality-client daemon is not running" in capsys.readouterr().err


def test_pause_resume_when_daemon_not_running(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    assert main(["pause", "30m"]) == 1
    assert main(["resume"]) == 1
    assert capsys.readouterr().err.count("vitality-client daemon is not running") == 2


def test_pause_invalid_duration(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    assert main(["pause", "3d"]) == 2 and "invalid duration: 3d" in capsys.readouterr().err


def test_format_status_idle():
    lines = format_status(STATUS).splitlines()
    assert lines[0].startswith("capturing · 412 captures today · last ")
    assert lines[0].endswith(" · 3.1 GB")
    assert "activity: available" in lines and "capture: available" in lines
    assert not any("warning" in line for line in lines)
    assert any("/c" in line for line in lines) and any("/d" in line for line in lines)


def test_format_status_paused_and_warning():
    s = status(state="paused", captures_today=3, last_capture=None, disk_bytes=25_000_000_000,
               disk_warning=True)
    out = format_status(s)
    assert out.splitlines()[0].startswith("paused · 3 captures today") and "disk" in out and "/d" in out


def test_format_status_paused_until_and_unavailable():
    s = status(state="paused", paused_until="2026-10-21T13:30:00.000Z", activity="unavailable")
    first = format_status(s).splitlines()[0]
    assert first.startswith("paused until ") and " · 412 captures today" in first
    assert "activity: unavailable" in format_status(s)


def test_format_status_upload_line():
    s = status(upload={"state": "offline", "pending": 12, "last_upload": "2026-10-25T16:58:02.000Z"})
    lines = format_status(s).splitlines()
    line = next(line for line in lines if line.startswith("upload:"))
    assert line.startswith("upload: offline · 12 pending · last ")
    assert not line.endswith("never")
    assert lines.index(line) == lines.index("capture: available") + 1


def test_format_status_upload_never_uploaded():
    s = status(upload={"state": "ok", "pending": 0, "last_upload": None})
    assert "upload: ok · 0 pending · last never" in format_status(s).splitlines()


def test_format_status_upload_disabled():
    assert "upload: disabled" in format_status(STATUS).splitlines()


def test_status_json_is_one_line_without_ok(tmp_path, monkeypatch, capsys):
    import asyncio
    from vitality_client.shared.channel import UnixControlServer

    sock = tmp_path / "vitality/client.sock"
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))

    async def handler(req: Request) -> Reply:
        return status()

    async def scenario():
        server = UnixControlServer(sock)
        await server.start(handler)
        try:
            return await asyncio.to_thread(main, ["status", "--json"])
        finally:
            await server.close()

    assert asyncio.run(scenario()) == 0
    out = capsys.readouterr().out
    assert out.count("\n") == 1 and "ok" not in json.loads(out)


def test_missing_runtime_dir_is_an_error_not_a_traceback(monkeypatch, capsys):
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    assert main(["status"]) == 1
    assert "XDG_RUNTIME_DIR is not set" in capsys.readouterr().err


def test_capture_state_line_daemon_not_running():
    from datetime import datetime
    from vitality_client.cli.doctor import capture_state_line
    from vitality_client.shared.state import State

    until = datetime(2026, 10, 21, 15, 30).timestamp()
    assert capture_state_line(None, State()) == "daemon: not running; saved state: not paused"
    assert capture_state_line(None, State(paused=True)) == "daemon: not running; saved state: paused"
    assert capture_state_line(None, State(paused=True, paused_until=until)) == (
        "daemon: not running; saved state: paused until 15:30")


def test_capture_state_line_daemon_running():
    from vitality_client.cli.doctor import capture_state_line
    from vitality_client.shared.state import State

    assert capture_state_line(status(state="idle"), State(paused=True)) == (
        "daemon: running, capturing")
    assert capture_state_line(status(state="active"), State()) == (
        "daemon: running, capturing")
    assert capture_state_line(status(state="paused"), State()) == (
        "daemon: running, paused")


def test_doctor_prints_saved_pause_state(tmp_path, monkeypatch, capsys):
    from vitality_client.cli import checks as doctor
    from vitality_client.shared.state import State, StateFile

    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(doctor, "run_checks", lambda env, paths: [])
    StateFile(tmp_path / "state/vitality/client/state.json").save(State(paused=True))
    assert main(["doctor"]) == 0
    assert "daemon: not running; saved state: paused" in capsys.readouterr().out


def _toggle_against(tmp_path, monkeypatch, state):
    import asyncio
    from vitality_client.shared.channel import UnixControlServer

    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    requests = []

    async def handler(req: Request) -> Reply:
        requests.append(req)
        if req["cmd"] == "status":
            return status(state=state)
        return status(state="idle" if req["cmd"] == "resume" else "paused")

    async def scenario():
        server = UnixControlServer(tmp_path / "vitality/client.sock")
        await server.start(handler)
        try:
            return await asyncio.to_thread(main, ["toggle"])
        finally:
            await server.close()

    return asyncio.run(scenario()), requests


@pytest.mark.parametrize("state", ["idle", "active"])
def test_toggle_pauses_when_capturing(tmp_path, monkeypatch, capsys, state):
    code, requests = _toggle_against(tmp_path, monkeypatch, state)
    assert code == 0 and requests == [{"cmd": "status"}, {"cmd": "pause"}]
    assert capsys.readouterr().out.startswith("paused")


def test_toggle_resumes_when_paused(tmp_path, monkeypatch, capsys):
    code, requests = _toggle_against(tmp_path, monkeypatch, "paused")
    assert code == 0 and requests == [{"cmd": "status"}, {"cmd": "resume"}]
    assert capsys.readouterr().out.startswith("capturing")


def test_toggle_when_daemon_not_running(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    assert main(["toggle"]) == 1
    assert "vitality-client daemon is not running" in capsys.readouterr().err


def _capture_against(tmp_path, monkeypatch, reply: Reply):
    import asyncio
    from vitality_client.shared.channel import UnixControlServer

    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    requests = []

    async def handler(req: Request) -> Reply:
        requests.append(req)
        return reply

    async def scenario():
        server = UnixControlServer(tmp_path / "vitality/client.sock")
        await server.start(handler)
        try:
            return await asyncio.to_thread(main, ["capture"])
        finally:
            await server.close()

    return asyncio.run(scenario()), requests


def test_capture_asks_the_daemon_and_prints_the_path(tmp_path, monkeypatch, capsys):
    code, requests = _capture_against(tmp_path, monkeypatch, {"ok": True, "path": "/d/c.webp"})
    assert code == 0 and requests == [{"cmd": "capture"}]
    assert capsys.readouterr().out.strip() == "/d/c.webp"


def test_capture_reports_the_daemons_error(tmp_path, monkeypatch, capsys):
    code, _ = _capture_against(tmp_path, monkeypatch, {"ok": False, "error": "no screen frame"})
    assert code == 1 and "no screen frame" in capsys.readouterr().err


def test_capture_when_daemon_not_running(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    assert main(["capture"]) == 1
    assert "vitality-client daemon is not running" in capsys.readouterr().err
