import asyncio

import pytest

from vitality_client.cli import main
from typing import cast

from vitality_client.shared.channel import Reply, Request, UnixControlServer


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    data = tmp_path / "data/vitality/client"
    state = tmp_path / "state/vitality/client"
    (data / "captures/2026-10-21").mkdir(parents=True)
    for name in ("a", "b", "c"):
        (data / f"captures/2026-10-21/{name}.webp").write_bytes(b"x" * 1000)
    (data / "index.sqlite").write_bytes(b"db")
    state.mkdir(parents=True)
    (state / "state.json").write_text('{"paused": false}')
    return data, state


def answer(monkeypatch, text):
    def fake_input(prompt=""):
        if text is None:
            raise EOFError
        return text
    monkeypatch.setattr("builtins.input", fake_input)


def test_reset_yes_deletes_data_and_keeps_state(dirs, capsys):
    data, state = dirs
    assert main(["reset", "--yes"]) == 0
    assert not data.exists() and (state / "state.json").exists()
    out = capsys.readouterr().out
    assert str(data) in out and "3 captures" in out


def test_reset_all_also_deletes_state(dirs):
    data, state = dirs
    assert main(["reset", "--all", "--yes"]) == 0
    assert not data.exists() and not state.exists()


def test_reset_prompt_yes_deletes(dirs, monkeypatch):
    data, _ = dirs
    answer(monkeypatch, "yes")
    assert main(["reset"]) == 0
    assert not data.exists()


@pytest.mark.parametrize("reply", ["no", "y", "", None])
def test_reset_prompt_anything_else_aborts(dirs, monkeypatch, capsys, reply):
    data, state = dirs
    answer(monkeypatch, reply)
    assert main(["reset", "--all"]) == 1
    assert data.exists() and state.exists()
    assert "nothing deleted" in capsys.readouterr().err


def test_reset_with_nothing_to_delete(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    assert main(["reset", "--all"]) == 0
    assert "nothing to delete" in capsys.readouterr().out


def test_reset_refuses_while_daemon_is_running(dirs, tmp_path, monkeypatch, capsys):
    data, state = dirs
    answer(monkeypatch, "yes")

    async def handler(req: Request) -> Reply:
        return cast(Reply, {"ok": True})  # reset only checks that something answers

    async def scenario():
        server = UnixControlServer(tmp_path / "run/vitality/client.sock")
        await server.start(handler)
        try:
            return await asyncio.to_thread(main, ["reset", "--all", "--yes"])
        finally:
            await server.close()

    assert asyncio.run(scenario()) == 1
    assert data.exists() and state.exists()
    assert "daemon is running; stop it first" in capsys.readouterr().err
