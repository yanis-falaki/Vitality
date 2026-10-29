import logging

from vitality_server.daemon import main as server_main
from vitality_server.shared.config import ServerConfig


def test_no_tailscale_never_falls_back_to_all_interfaces(tmp_path, capsys, monkeypatch):
    async def failing(run=None): raise RuntimeError("tailscale is not running")
    monkeypatch.setattr(server_main, "tailscale_ipv4", failing)
    monkeypatch.setattr(server_main.uvicorn, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not serve")))
    assert server_main.serve(ServerConfig(data_dir=tmp_path / "data")) == 1
    assert "tailscale" in capsys.readouterr().err


def test_serves_on_the_tailscale_address(tmp_path, monkeypatch):
    async def ip(run=None): return "100.64.0.9"
    monkeypatch.setattr(server_main, "tailscale_ipv4", ip)
    served = {}
    monkeypatch.setattr(server_main.uvicorn, "run", lambda app, **k: served.update(k))
    assert server_main.serve(ServerConfig(data_dir=tmp_path / "data", port=9123)) == 0
    assert served == {"host": "100.64.0.9", "port": 9123, "proxy_headers": False}


def test_warns_when_no_client_is_allowed(tmp_path, monkeypatch, caplog):
    async def ip(run=None): return "100.64.0.9"
    monkeypatch.setattr(server_main, "tailscale_ipv4", ip)
    monkeypatch.setattr(server_main.uvicorn, "run", lambda app, **k: None)
    with caplog.at_level(logging.WARNING):
        server_main.serve(ServerConfig(data_dir=tmp_path / "data"))
    assert "SERVER_ALLOWED_CLIENTS is empty" in caplog.text


def test_unusable_data_dir_returns_1(tmp_path, capsys):
    (tmp_path / "afile").write_text("")
    assert server_main.serve(ServerConfig(data_dir=tmp_path / "afile" / "data")) == 1
    assert capsys.readouterr().err.startswith("vitality-server: ")


def test_recover_failure_returns_1(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(server_main.FileStore, "recover", lambda self: (_ for _ in ()).throw(OSError(5, "I/O error")))
    monkeypatch.setattr(server_main.uvicorn, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not serve")))
    assert server_main.serve(ServerConfig(data_dir=tmp_path / "data")) == 1
    assert capsys.readouterr().err.startswith("vitality-server: ")
