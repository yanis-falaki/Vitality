from pathlib import Path

import pytest

from vitality_client.config import Config, load_config, resolve_paths


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
