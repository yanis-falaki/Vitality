from vitality_server.daemon import main as server_main


def test_bad_config_returns_1(tmp_path):
    path = tmp_path / "server.toml"
    path.write_text("bogus = 1\n")
    assert server_main.main(["--config", str(path)]) == 1


def test_bad_config_message_goes_to_stderr(tmp_path, capsys):
    path = tmp_path / "server.toml"
    path.write_text("bogus = 1\n")
    server_main.main(["--config", str(path)])
    err = capsys.readouterr().err
    assert err.startswith("vitality-serverd: ") and "bogus" in err


def test_no_tailscale_never_falls_back_to_all_interfaces(tmp_path, capsys, monkeypatch):
    path = tmp_path / "server.toml"
    path.write_text(f'data_dir = "{tmp_path / "data"}"\n')

    async def failing(run=None): raise RuntimeError("tailscale is not running")
    monkeypatch.setattr(server_main, "tailscale_ipv4", failing)
    monkeypatch.setattr(server_main.uvicorn, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not serve")))
    assert server_main.main(["--config", str(path)]) == 1
    assert "tailscale" in capsys.readouterr().err


def test_serves_on_the_tailscale_address(tmp_path, monkeypatch):
    path = tmp_path / "server.toml"
    path.write_text(f'data_dir = "{tmp_path / "data"}"\nport = 9123\n')

    async def ip(run=None): return "100.64.0.9"
    monkeypatch.setattr(server_main, "tailscale_ipv4", ip)
    served = {}
    monkeypatch.setattr(server_main.uvicorn, "run", lambda app, **k: served.update(k))
    assert server_main.main(["--config", str(path)]) == 0
    assert served == {"host": "100.64.0.9", "port": 9123, "proxy_headers": False}


def test_unusable_data_dir_returns_1(tmp_path, capsys):
    (tmp_path / "afile").write_text("")
    path = tmp_path / "server.toml"
    path.write_text(f'data_dir = "{tmp_path / "afile" / "data"}"\n')
    assert server_main.main(["--config", str(path)]) == 1
    assert capsys.readouterr().err.startswith("vitality-serverd: ")


def test_recover_failure_returns_1(tmp_path, capsys, monkeypatch):
    path = tmp_path / "server.toml"
    path.write_text(f'data_dir = "{tmp_path / "data"}"\n')
    monkeypatch.setattr(server_main.FileStore, "recover", lambda self: (_ for _ in ()).throw(OSError(5, "I/O error")))
    monkeypatch.setattr(server_main.uvicorn, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not serve")))
    assert server_main.main(["--config", str(path)]) == 1
    assert capsys.readouterr().err.startswith("vitality-serverd: ")
