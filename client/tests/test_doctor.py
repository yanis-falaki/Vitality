from pathlib import Path

from vitality_client import doctor
from vitality_client.config import Paths
from vitality_client.doctor import Check, run_checks


def run_checks_with(groups, tmp_path, env=None, portal=True, gst=True):
    paths = Paths(tmp_path / "config/client.toml", tmp_path / "data",
                  tmp_path / "state/state.json", tmp_path / "run/vitality/client.sock")
    env = env if env is not None else {"XDG_SESSION_TYPE": "wayland", "XDG_RUNTIME_DIR": str(tmp_path)}
    return run_checks(env, paths, groups=groups, portal_probe=lambda: portal, gst_probe=lambda: gst)


def test_doctor_reports_missing_input_group(tmp_path):
    checks = {c.name: c for c in run_checks_with(groups=["wheel"], tmp_path=tmp_path)}
    assert not checks["input group"].ok and "usermod -aG input" in checks["input group"].hint


def test_doctor_all_ok(tmp_path):
    (tmp_path / "pipewire-0").touch()
    checks = run_checks_with(groups=["wheel", "input"], tmp_path=tmp_path)
    assert [c.name for c in checks] == [
        "input group", "wayland session", "portal", "pipewire",
        "gstreamer pipewire plugin", "directories writable",
    ]
    assert all(isinstance(c, Check) and c.ok for c in checks), [c for c in checks if not c.ok]


def test_doctor_environment_failures(tmp_path):
    env = {"XDG_SESSION_TYPE": "x11", "XDG_RUNTIME_DIR": str(tmp_path)}  # no pipewire-0 socket
    checks = {c.name: c for c in run_checks_with(["input"], tmp_path, env=env, portal=False, gst=False)}
    assert not checks["wayland session"].ok and "x11" in checks["wayland session"].hint
    assert not checks["portal"].ok and not checks["pipewire"].ok
    assert not checks["gstreamer pipewire plugin"].ok
    assert "gst-plugin-pipewire" in checks["gstreamer pipewire plugin"].hint


def test_pipewire_without_runtime_dir():
    assert not doctor.check_pipewire({}).ok


def test_probe_exception_is_a_failed_check(tmp_path):
    def boom():
        raise OSError("no bus")
    c = doctor.check_portal(boom)
    assert not c.ok and "no bus" in c.hint


def test_directories_writable_checks_nearest_existing_parent(tmp_path):
    paths = Paths(tmp_path / "c.toml", tmp_path / "a/b/data", tmp_path / "a/b/state/state.json",
                  tmp_path / "run/client.sock")
    assert doctor.check_directories(paths).ok
    tmp_path.chmod(0o500)
    try:
        c = doctor.check_directories(paths)
    finally:
        tmp_path.chmod(0o700)
    assert not c.ok and str(Path(tmp_path / "a/b/data")) in c.hint
