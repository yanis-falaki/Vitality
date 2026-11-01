import asyncio
import logging

import pytest

from vitality_server.daemon import main as server_main
from vitality_server.shared.config import ServerConfig

IP = "100.64.0.9"


class ScriptedIPv4:
    """A fake `tailscale_ipv4`: answers from `script` in order (an exception is raised, a string
    returned), then repeats the last answer forever. Counts its calls."""

    def __init__(self, *script: str | Exception) -> None:
        self.script = list(script)
        self.calls = 0

    async def __call__(self) -> str:
        answer = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        if isinstance(answer, Exception):
            raise answer
        return answer


DOWN = RuntimeError("`tailscale ip -4` gave no address (exit code 1)")


class FakeServer:
    """Stands in for uvicorn.Server: serves until told to exit, or, standing for a signal,
    until the fake ipv4 has been asked `stop_after_calls` times."""

    made: list["FakeServer"] = []
    ipv4: ScriptedIPv4
    stop_after_calls = 10**9

    def __init__(self, config) -> None:
        self.config = config
        self.should_exit = False
        self.stopped_by_watchdog = False
        FakeServer.made.append(self)

    async def serve(self) -> None:
        while not self.should_exit:
            if self.ipv4.calls >= self.stop_after_calls:
                return
            await asyncio.sleep(0)
        self.stopped_by_watchdog = True


@pytest.fixture
def fake_server(monkeypatch):
    FakeServer.made = []
    FakeServer.stop_after_calls = 10**9
    monkeypatch.setattr(server_main.uvicorn, "Server", FakeServer)
    return FakeServer


def run_serve(tmp_path, ipv4: ScriptedIPv4, fake_server, stop_after_calls=10**9, **config):
    fake_server.ipv4 = ipv4
    fake_server.stop_after_calls = stop_after_calls
    return server_main.serve(ServerConfig(data_dir=tmp_path / "data", **config),
                             ipv4=ipv4, check_interval_s=0.001, failure_limit=3)


# -- start-up -----------------------------------------------------------------------------------

def test_no_tailscale_never_falls_back_to_all_interfaces(tmp_path, capsys, fake_server):
    assert run_serve(tmp_path, ScriptedIPv4(RuntimeError("tailscale is not running")), fake_server) == 1
    assert fake_server.made == []  # nothing served
    assert "tailscale" in capsys.readouterr().err


def test_warns_when_no_client_is_allowed(tmp_path, caplog, fake_server):
    with caplog.at_level(logging.WARNING):
        run_serve(tmp_path, ScriptedIPv4(IP), fake_server, stop_after_calls=2)
    assert "SERVER_ALLOWED_CLIENTS is empty" in caplog.text


def test_unusable_data_dir_returns_1(tmp_path, capsys):
    (tmp_path / "afile").write_text("")
    assert server_main.serve(ServerConfig(data_dir=tmp_path / "afile" / "data")) == 1
    assert capsys.readouterr().err.startswith("vitality-server: ")


def test_recover_failure_returns_1(tmp_path, capsys, monkeypatch, fake_server):
    monkeypatch.setattr(server_main.FileStore, "recover", lambda self: (_ for _ in ()).throw(OSError(5, "I/O error")))
    assert run_serve(tmp_path, ScriptedIPv4(IP), fake_server) == 1
    assert fake_server.made == []
    assert capsys.readouterr().err.startswith("vitality-server: ")


# -- serving and the watchdog -------------------------------------------------------------------

def test_serves_on_the_tailscale_address_until_stopped(tmp_path, monkeypatch, fake_server, caplog):
    closed = []
    monkeypatch.setattr(server_main.FileStore, "close", lambda self: closed.append(True))
    ipv4 = ScriptedIPv4(IP)
    with caplog.at_level(logging.WARNING):
        code = run_serve(tmp_path, ipv4, fake_server, stop_after_calls=10, port=9123,
                         allowed_clients=("laptop",))
    assert code == 0
    [server] = fake_server.made
    assert (server.config.host, server.config.port, server.config.proxy_headers) == (IP, 9123, False)
    assert not server.stopped_by_watchdog
    assert ipv4.calls >= 10  # the watchdog kept checking while serving
    assert closed == [True]
    assert caplog.text == ""


def test_exits_after_three_failures_in_a_row(tmp_path, monkeypatch, fake_server, caplog):
    closed = []
    monkeypatch.setattr(server_main.FileStore, "close", lambda self: closed.append(True))
    ipv4 = ScriptedIPv4(IP, DOWN)  # found at start, then down for good
    with caplog.at_level(logging.WARNING):
        assert run_serve(tmp_path, ipv4, fake_server, allowed_clients=("laptop",)) == 1
    assert ipv4.calls == 1 + 3
    assert fake_server.made[0].stopped_by_watchdog
    assert closed == [True]
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "Tailscale unreachable" in warnings[0] and "fresh /var/run/tailscale" in warnings[0]


def test_two_failures_are_not_enough(tmp_path, fake_server):
    ipv4 = ScriptedIPv4(IP, DOWN, DOWN, IP)  # two failures, then fine
    assert run_serve(tmp_path, ipv4, fake_server, stop_after_calls=20) == 0
    assert not fake_server.made[0].stopped_by_watchdog


def test_a_success_resets_the_failure_count(tmp_path, fake_server):
    ipv4 = ScriptedIPv4(IP, DOWN, DOWN, IP, DOWN, DOWN, IP, DOWN, DOWN, IP)  # never 3 in a row
    assert run_serve(tmp_path, ipv4, fake_server, stop_after_calls=20) == 0
    assert not fake_server.made[0].stopped_by_watchdog


def test_exits_when_the_address_changes(tmp_path, fake_server, caplog):
    ipv4 = ScriptedIPv4(IP, IP, "100.64.0.10")
    with caplog.at_level(logging.WARNING):
        assert run_serve(tmp_path, ipv4, fake_server) == 1
    assert ipv4.calls == 3
    assert fake_server.made[0].stopped_by_watchdog
    assert "changed from 100.64.0.9 to 100.64.0.10" in caplog.text


def test_watchdog_counts_failures_one_check_per_interval():
    async def scenario() -> tuple[str, int]:
        ipv4 = ScriptedIPv4(DOWN)
        reason = await server_main._watch_tailscale(IP, ipv4, 0.001, 3)
        return reason, ipv4.calls
    reason, calls = asyncio.run(scenario())
    assert calls == 3 and "unreachable for 3 checks" in reason
