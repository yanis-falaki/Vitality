from pathlib import Path

import pytest
from vitality_server.shared.config import ServerConfig, load_server_config


def write(tmp_path, text):
    path = tmp_path / "server.toml"
    path.write_text(text)
    return path


def test_defaults(tmp_path):
    expected = ServerConfig(data_dir=Path("/srv/vitality"), port=8100, allowed_clients=())
    assert load_server_config(write(tmp_path, "")) == expected
    assert load_server_config(tmp_path / "missing.toml") == expected


def test_full_file_loads(tmp_path):
    path = write(tmp_path, 'data_dir = "/data/v"\nport = 9000\nallowed_clients = ["laptop", "desk"]\n')
    assert load_server_config(path) == ServerConfig(
        data_dir=Path("/data/v"), port=9000, allowed_clients=("laptop", "desk"))


@pytest.mark.parametrize("text,key", [
    ("colour = 1", "colour"),
    ('port = "x"', "port"),
    ("port = true", "port"),
    ("port = 0", "port"),
    ("port = 70000", "port"),
    ('allowed_clients = "laptop"', "allowed_clients"),
    ("allowed_clients = [1]", "allowed_clients"),
    ("data_dir = 5", "data_dir"),
    ('data_dir = ""', "data_dir"),
])
def test_bad_values_raise_naming_the_key(tmp_path, text, key):
    with pytest.raises(ValueError, match=key):
        load_server_config(write(tmp_path, text))
