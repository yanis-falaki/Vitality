from vitality_client.daemon.main import main


def test_refuses_non_wayland(monkeypatch, capsys):
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    assert main([]) == 1
    assert "vitality-clientd requires a Wayland session (XDG_SESSION_TYPE=x11)" in capsys.readouterr().err


def test_missing_runtime_dir_is_an_error_not_a_traceback(monkeypatch, capsys):
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    assert main([]) == 1
    assert "vitality-clientd: XDG_RUNTIME_DIR is not set" in capsys.readouterr().err


def test_old_layout_is_an_error_and_nothing_is_deleted(monkeypatch, tmp_path, capsys):
    import sqlite3

    data = tmp_path / "data/vitality/client"
    shot = data / "captures/2025-10-21/01JAB0000000000000000000.webp"
    shot.parent.mkdir(parents=True); shot.write_bytes(b"RIFF....WEBP")
    conn = sqlite3.connect(data / "index.sqlite")
    conn.execute("CREATE TABLE captures (capture_id TEXT PRIMARY KEY)"); conn.commit(); conn.close()
    for var, sub in [("XDG_DATA_HOME", "data"), ("XDG_CONFIG_HOME", "config"),
                     ("XDG_STATE_HOME", "state"), ("XDG_RUNTIME_DIR", "run")]:
        monkeypatch.setenv(var, str(tmp_path / sub))
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    monkeypatch.setattr("vitality_client.daemon.frames.portal.PortalFrameSource",
                        lambda token: object())
    monkeypatch.setattr("vitality_client.daemon.activity.ActivityWatcher", lambda: object())
    assert main([]) == 1
    err = capsys.readouterr().err
    assert "vitality-clientd: old capture layout found; run the one-time migration first" in err
    assert "client/README.md" in err
    assert shot.read_bytes() == b"RIFF....WEBP"
    assert sorted(p.name for p in data.iterdir()) == ["captures", "index.sqlite"]
