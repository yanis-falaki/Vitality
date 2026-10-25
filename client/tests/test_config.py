from pathlib import Path

import pytest

from vitality_client.shared.config import Config, load_config, resolve_paths


def test_resolve_paths_defaults(tmp_path):
    p = resolve_paths({"XDG_RUNTIME_DIR": "/run/user/1000"}, home=tmp_path)
    assert p.config_file == tmp_path / ".config/vitality/client.toml"
    assert p.data_dir == tmp_path / ".local/share/vitality/client"
    assert p.state_file == tmp_path / ".local/state/vitality/client/state.json"
    assert p.socket_path == Path("/run/user/1000/vitality/client.sock")


def test_resolve_paths_respects_xdg_vars(tmp_path):
    env = {"XDG_CONFIG_HOME": "/c", "XDG_DATA_HOME": "/d", "XDG_STATE_HOME": "/s", "XDG_RUNTIME_DIR": "/r"}
    p = resolve_paths(env, home=tmp_path)
    assert p.config_file == Path("/c/vitality/client.toml")
    assert p.data_dir == Path("/d/vitality/client")
    assert p.state_file == Path("/s/vitality/client/state.json")
    assert p.socket_path == Path("/r/vitality/client.sock")


def test_resolve_paths_missing_runtime_dir(tmp_path):
    with pytest.raises(RuntimeError, match="XDG_RUNTIME_DIR is not set"):
        resolve_paths({}, home=tmp_path)


def test_load_config_missing_file_gives_defaults(tmp_path):
    assert load_config(tmp_path / "nope.toml") == Config()


def test_load_config_overrides(tmp_path):
    f = tmp_path / "client.toml"
    f.write_text("settle_seconds = 3\nwebp_quality = 90\n")
    c = load_config(f)
    assert (c.settle_seconds, c.webp_quality, c.max_gap_seconds) == (3, 90, 10)
    assert isinstance(c.settle_seconds, float)


def test_load_config_float_values(tmp_path):
    f = tmp_path / "client.toml"
    f.write_text("max_gap_seconds = 7.5\ndisk_warning_gb = 5\n")
    c = load_config(f)
    assert c.max_gap_seconds == 7.5
    assert c.disk_warning_gb == 5.0 and isinstance(c.disk_warning_gb, float)


def test_load_config_unknown_key_rejected(tmp_path):
    f = tmp_path / "client.toml"
    f.write_text("settle = 3\n")
    with pytest.raises(ValueError, match="settle"):
        load_config(f)


@pytest.mark.parametrize(
    "line",
    [
        'settle_seconds = "3"',
        "settle_seconds = true",
        "webp_quality = 85.5",
        'webp_quality = "90"',
        "webp_quality = false",
        "disk_warning_gb = [1]",
    ],
)
def test_load_config_wrong_type_rejected(tmp_path, line):
    f = tmp_path / "client.toml"
    f.write_text(line + "\n")
    with pytest.raises(ValueError):
        load_config(f)


@pytest.mark.parametrize(
    "line, key",
    [
        ("settle_seconds = 0", "settle_seconds"),
        ("settle_seconds = -1.5", "settle_seconds"),
        ("max_gap_seconds = 0", "max_gap_seconds"),
        ("max_gap_seconds = -10", "max_gap_seconds"),
        ("disk_warning_gb = -1", "disk_warning_gb"),
        ("webp_quality = 0", "webp_quality"),
        ("webp_quality = 101", "webp_quality"),
        ("webp_quality = -5", "webp_quality"),
        ("settle_seconds = nan", "settle_seconds"),
        ("max_gap_seconds = inf", "max_gap_seconds"),
        ("disk_warning_gb = inf", "disk_warning_gb"),
        ("disk_warning_gb = -inf", "disk_warning_gb"),
    ],
)
def test_load_config_out_of_range_rejected(tmp_path, line, key):
    f = tmp_path / "client.toml"
    f.write_text(line + "\n")
    with pytest.raises(ValueError, match=key):
        load_config(f)


def test_load_config_range_boundaries_accepted(tmp_path):
    f = tmp_path / "client.toml"
    f.write_text("settle_seconds = 0.1\nmax_gap_seconds = 0.5\ndisk_warning_gb = 0\nwebp_quality = 1\n")
    c = load_config(f)
    assert (c.settle_seconds, c.max_gap_seconds, c.disk_warning_gb, c.webp_quality) == (0.1, 0.5, 0.0, 1)
    f.write_text("webp_quality = 100\n")
    assert load_config(f).webp_quality == 100


def test_upload_server_loaded(tmp_path):
    p = tmp_path / "client.toml"
    p.write_text('upload_server = "http://vitality-librarian:8100"\n')
    assert load_config(p).upload_server == "http://vitality-librarian:8100"


def test_upload_server_trailing_slash_stripped(tmp_path):
    p = tmp_path / "client.toml"
    p.write_text('upload_server = "https://vitality-librarian:8100/"\n')
    assert load_config(p).upload_server == "https://vitality-librarian:8100"


def test_upload_server_defaults_to_none(tmp_path):
    assert load_config(tmp_path / "missing.toml").upload_server is None


@pytest.mark.parametrize("value", ["8100", '"vitality-librarian:8100"', '""'])
def test_upload_server_must_be_http_url(tmp_path, value):
    p = tmp_path / "client.toml"
    p.write_text(f"upload_server = {value}\n")
    with pytest.raises(ValueError, match="upload_server"):
        load_config(p)


@pytest.mark.parametrize("value", [
    "http://", "http:///", "https://:8100", "http://host:81000", "http://host:0",
    "ftp://host", "http://host:port", "http://[::1",
])
def test_upload_server_must_have_a_host_and_a_valid_port(tmp_path, value):
    p = tmp_path / "client.toml"
    p.write_text(f'upload_server = "{value}"\n')
    with pytest.raises(ValueError, match="upload_server"):
        load_config(p)


@pytest.mark.parametrize("value", ["http://100.101.102.103:8100", "https://host", "http://host:65535"])
def test_upload_server_valid_urls(tmp_path, value):
    p = tmp_path / "client.toml"
    p.write_text(f'upload_server = "{value}"\n')
    assert load_config(p).upload_server == value
