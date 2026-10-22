from pathlib import Path

from vitality_client.cli import checks as doctor
from vitality_client.config import Paths
from vitality_client.cli.checks import Check, run_checks


def run_checks_with(groups, tmp_path, env=None, portal=True, gi=True, missing=()):
    paths = Paths(tmp_path / "config/client.toml", tmp_path / "data",
                  tmp_path / "state/state.json", tmp_path / "run/vitality/client.sock")
    env = env if env is not None else {"XDG_SESSION_TYPE": "wayland", "XDG_RUNTIME_DIR": str(tmp_path)}
    return run_checks(env, paths, groups=groups, portal_probe=lambda: portal,
                      gi_probe=lambda: gi, gst_probe=lambda: list(missing))


def test_doctor_reports_missing_input_group(tmp_path):
    checks = {c.name: c for c in run_checks_with(groups=["wheel"], tmp_path=tmp_path)}
    assert not checks["input group"].ok and "usermod -aG input" in checks["input group"].hint


def test_doctor_all_ok(tmp_path):
    (tmp_path / "pipewire-0").touch()
    checks = run_checks_with(groups=["wheel", "input"], tmp_path=tmp_path)
    assert [c.name for c in checks] == [
        "input group", "wayland session", "portal", "pipewire",
        "python gobject", "gstreamer elements", "directories writable",
    ]
    assert all(isinstance(c, Check) and c.ok for c in checks), [c for c in checks if not c.ok]


def test_doctor_environment_failures(tmp_path):
    env = {"XDG_SESSION_TYPE": "x11", "XDG_RUNTIME_DIR": str(tmp_path)}  # no pipewire-0 socket
    checks = {c.name: c for c in run_checks_with(["input"], tmp_path, env=env, portal=False,
                                                          missing=["pipewiresrc"])}
    assert not checks["wayland session"].ok and "x11" in checks["wayland session"].hint
    assert not checks["portal"].ok and not checks["pipewire"].ok
    assert not checks["gstreamer elements"].ok
    assert "gst-plugin-pipewire" in checks["gstreamer elements"].hint


def test_gst_check_names_every_missing_element(tmp_path):
    checks = {c.name: c for c in run_checks_with(
        ["input"], tmp_path, missing=["pipewiresrc", "appsink"])}
    c = checks["gstreamer elements"]
    assert not c.ok and "pipewiresrc, appsink" in c.hint
    assert "sudo pacman -S gst-plugins-base gst-plugin-pipewire" in c.hint
    assert checks["python gobject"].ok


def test_gst_elements_cover_the_pipeline():
    from vitality_client.frames.portal.stream import pipeline_description
    description = pipeline_description(3, 42, 5)
    assert set(doctor.GST_ELEMENTS) == {"pipewiresrc", "appsink"}
    assert all(e in description for e in doctor.GST_ELEMENTS)
    assert "videorate" not in description and "videoconvert" not in description


def test_missing_gi_is_reported_separately(tmp_path):
    def no_gi():
        raise ImportError("No module named 'gi'")
    checks = {c.name: c for c in run_checks(
        {"XDG_SESSION_TYPE": "wayland", "XDG_RUNTIME_DIR": str(tmp_path)},
        Paths(tmp_path / "c.toml", tmp_path / "d", tmp_path / "s/state.json", tmp_path / "r/c.sock"),
        groups=["input"], portal_probe=lambda: True, gi_probe=no_gi,
        gst_probe=lambda: ["pipewiresrc"])}
    gi = checks["python gobject"]
    assert not gi.ok and "sudo pacman -S python-gobject" in gi.hint
    assert "system site packages" in gi.hint and "No module named 'gi'" in gi.hint
    assert not checks["gstreamer elements"].ok
    assert "pipewiresrc" not in checks["gstreamer elements"].hint  # not blamed on the plugin


def test_real_probes_run_here():
    assert doctor.gi_importable() is True
    assert isinstance(doctor.missing_gst_elements(), list)


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
