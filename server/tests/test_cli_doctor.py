import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from vitality_server.cli import doctor as doctor_module
from vitality_server.cli.checks import Check, run_checks, server_healthy
from vitality_server.cli.doctor import doctor

GB = 1_000_000_000


def make_config(tmp_path, clients='["laptop", "desk"]', data=True):
    data_dir = tmp_path / "data"
    if data:
        data_dir.mkdir()
    path = tmp_path / "server.toml"
    path.write_text(f'data_dir = "{data_dir}"\nport = 9123\nallowed_clients = {clients}\n')
    return path


def run(config, tailscale_probe=lambda: "100.64.0.9", service_probe=lambda: True,
        health_probe=lambda ip, port: True, disk_probe=lambda path: 19_700_000_000):
    """`run_checks` with every probe fake-ok unless a test replaces it."""
    return run_checks(config, tailscale_probe, service_probe, health_probe, disk_probe)


def raising(exc):
    def probe(*args):
        raise exc
    return probe


def test_all_ok_in_order_with_details_in_the_names(tmp_path):
    checks = run(make_config(tmp_path))
    assert [c.name for c in checks] == [
        "config loads", "tailscale up (100.64.0.9)", "service running",
        "/health answers on 100.64.0.9:9123", "data folder present",
        "allowed_clients set (laptop, desk)", "disk space (19.7 GB free)"]
    assert all(isinstance(c, Check) and c.ok and c.hint == "" for c in checks), checks


def test_missing_config_file_is_ok_but_says_it_uses_defaults(tmp_path):
    checks = run(tmp_path / "absent.toml")
    assert checks[0] == Check("config loads (no file; using defaults)", True, "")


def test_health_probe_gets_the_tailscale_address_and_port(tmp_path):
    seen = []
    run(make_config(tmp_path), health_probe=lambda ip, port: seen.append((ip, port)) or True)
    assert seen == [("100.64.0.9", 9123)]


def failing(checks, name_start):
    (check,) = [c for c in checks if c.name.startswith(name_start)]
    assert not check.ok
    return check


def test_tailscale_down(tmp_path):
    checks = run(make_config(tmp_path), tailscale_probe=raising(RuntimeError("not running")))
    c = failing(checks, "tailscale up")
    assert c.name == "tailscale up"
    assert "sudo tailscale up --hostname=vitality-librarian" in c.hint and "not running" in c.hint
    health = failing(checks, "/health")
    assert "tailscale" in health.hint


def test_service_not_running(tmp_path):
    c = failing(run(make_config(tmp_path), service_probe=lambda: False), "service running")
    assert c.hint == "sudo systemctl start vitality-serverd; logs: journalctl -u vitality-serverd -n 50"


def test_service_probe_raising_is_a_failed_check(tmp_path):
    c = failing(run(make_config(tmp_path), service_probe=raising(OSError("no systemctl"))), "service running")
    assert "journalctl" in c.hint and "no systemctl" in c.hint


def test_health_not_answering(tmp_path):
    c = failing(run(make_config(tmp_path), health_probe=lambda ip, port: False), "/health")
    assert c.name == "/health answers on 100.64.0.9:9123"
    assert c.hint == "is the service running? see journalctl -u vitality-serverd"


def test_health_probe_raising_is_a_failed_check(tmp_path):
    c = failing(run(make_config(tmp_path), health_probe=raising(TimeoutError("timed out"))), "/health")
    assert "journalctl" in c.hint and "timed out" in c.hint


def test_data_folder_missing(tmp_path):
    config = make_config(tmp_path, data=False)
    c = failing(run(config), "data folder")
    assert str(tmp_path / "data") in c.hint and "install.sh" in c.hint


def test_data_folder_that_is_a_file(tmp_path):
    config = make_config(tmp_path, data=False)
    (tmp_path / "data").write_text("")
    failing(run(config), "data folder")


def test_allowed_clients_empty(tmp_path):
    c = failing(run(make_config(tmp_path, clients="[]")), "allowed_clients")
    assert c.name == "allowed_clients set"
    assert c.hint == "edit {}/server.toml, then sudo systemctl restart vitality-serverd".format(tmp_path)


def test_disk_space_below_1_gb(tmp_path):
    c = failing(run(make_config(tmp_path), disk_probe=lambda path: GB - 1), "disk space")
    assert c.name == "disk space (1000.0 MB free)"
    assert "grow the VM's disk" in c.hint


def test_disk_space_of_exactly_1_gb_passes(tmp_path):
    checks = run(make_config(tmp_path), disk_probe=lambda path: GB)
    assert checks[-1].ok and checks[-1].name == "disk space (1.0 GB free)"


def test_disk_probe_gets_the_data_dir(tmp_path):
    seen = []
    run(make_config(tmp_path), disk_probe=lambda path: seen.append(path) or 5 * GB)
    assert seen == [tmp_path / "data"]


def test_disk_probe_raising_is_a_failed_check(tmp_path):
    c = failing(run(make_config(tmp_path), disk_probe=raising(FileNotFoundError("gone"))), "disk space")
    assert "gone" in c.hint


def test_bad_config_fails_the_checks_that_need_it(tmp_path):
    path = tmp_path / "server.toml"
    path.write_text("bogus = 1\n")
    checks = run(path)
    assert [c.name for c in checks] == [
        "config loads", "tailscale up (100.64.0.9)", "service running", "/health answers",
        "data folder present", "allowed_clients set", "disk space"]
    assert not checks[0].ok and "bogus" in checks[0].hint
    assert checks[1].ok and checks[2].ok  # these do not need the config
    for c in checks[3:]:
        assert not c.ok and c.hint == "needs a valid config"


def test_unreadable_config_file_fails_the_first_check(tmp_path):
    (tmp_path / "server.toml").mkdir()  # reading a directory raises OSError
    assert not run(tmp_path / "server.toml")[0].ok


def test_real_probes_never_raise(tmp_path):
    from vitality_server.cli import checks as module
    assert isinstance(module.service_active(), bool)
    assert isinstance(module.disk_free_bytes(tmp_path), int)


@pytest.fixture
def health_server():
    """A local HTTP server whose /health reply is set by the test: (status, body)."""
    reply = {"status": 200, "body": b'{"ok":true,"disk_free_bytes":5}'}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(reply["status"])
            self.end_headers()
            self.wfile.write(reply["body"])

        def log_message(self, format, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield reply, server.server_port
    server.shutdown()
    server.server_close()


def test_real_health_probe_accepts_a_valid_answer(health_server):
    _, port = health_server
    assert server_healthy("127.0.0.1", port) is True


def test_real_health_probe_rejects_a_bad_body(health_server):
    reply, port = health_server
    reply["body"] = b"hello"
    assert server_healthy("127.0.0.1", port) is False


def test_real_health_probe_raises_on_an_error_status(health_server):
    reply, port = health_server
    reply["status"] = 500
    with pytest.raises(OSError):
        server_healthy("127.0.0.1", port)


def patch_checks(monkeypatch, **probes):
    """Make `doctor` run its checks with fake probes (fake-ok unless given)."""
    monkeypatch.setattr(doctor_module, "run_checks", lambda config: run(config, **probes))


def test_doctor_prints_each_check_then_the_paths_and_exits_0(tmp_path, capsys, monkeypatch):
    patch_checks(monkeypatch)
    config = make_config(tmp_path)
    assert doctor(config) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[:7] == [
        "✓ config loads", "✓ tailscale up (100.64.0.9)", "✓ service running",
        "✓ /health answers on 100.64.0.9:9123", "✓ data folder present",
        "✓ allowed_clients set (laptop, desk)", "✓ disk space (19.7 GB free)"]
    data = tmp_path / "data"
    assert lines[7:] == [
        f"config:   {config}", f"data:     {data}", f"captures: {data}/captures",
        f"index:    {data}/index.sqlite", "program:  /opt/vitality-server",
        "service:  /etc/systemd/system/vitality-serverd.service",
        "logs:     journalctl -u vitality-serverd"]


def test_doctor_failure_line_has_the_hint_and_exit_1(tmp_path, capsys, monkeypatch):
    patch_checks(monkeypatch, service_probe=lambda: False)
    assert doctor(make_config(tmp_path)) == 1
    out = capsys.readouterr().out
    assert ("✗ service running — sudo systemctl start vitality-serverd; "
            "logs: journalctl -u vitality-serverd -n 50") in out
    assert "✓ config loads" in out


def test_doctor_with_a_bad_config_still_prints_the_fixed_paths(tmp_path, capsys, monkeypatch):
    patch_checks(monkeypatch)
    path = tmp_path / "server.toml"
    path.write_text("bogus = 1\n")
    assert doctor(path) == 1
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("✗ config loads — ") and "bogus" in lines[0]
    assert "✗ /health answers — needs a valid config" in lines
    assert f"config:   {path}" in lines
    assert "program:  /opt/vitality-server" in lines
    assert not any(line.startswith("data:") for line in lines)
