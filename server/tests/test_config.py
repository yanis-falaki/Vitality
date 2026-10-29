from pathlib import Path

import pytest
from vitality_server.shared.config import ServerConfig, load_server_config


def test_defaults_with_no_variables():
    assert load_server_config({}) == ServerConfig(Path("/data"), 8100, ())


def test_reads_every_variable():
    env = {"SERVER_DATA_DIR": "/x", "SERVER_PORT": "9000", "SERVER_ALLOWED_CLIENTS": "laptop,phone"}
    assert load_server_config(env) == ServerConfig(Path("/x"), 9000, ("laptop", "phone"))


def test_allowed_clients_are_stripped_and_empties_dropped():
    assert load_server_config({"SERVER_ALLOWED_CLIENTS": " laptop , ,phone "}).allowed_clients == ("laptop", "phone")
    assert load_server_config({"SERVER_ALLOWED_CLIENTS": ""}).allowed_clients == ()


@pytest.mark.parametrize("port", ["abc", "0", "65536", "1.5", "-1", "1_000"])
def test_bad_port_names_the_variable(port):
    with pytest.raises(ValueError, match="SERVER_PORT"):
        load_server_config({"SERVER_PORT": port})


def test_empty_port_means_the_default():
    assert load_server_config({"SERVER_PORT": ""}).port == 8100


def test_empty_data_dir_is_refused():
    with pytest.raises(ValueError, match="SERVER_DATA_DIR"):
        load_server_config({"SERVER_DATA_DIR": ""})


def test_unrelated_variables_are_ignored():
    assert load_server_config({"PATH": "/bin", "OBSERVER_MODEL": "x"}) == ServerConfig()
