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
