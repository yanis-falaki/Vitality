from pathlib import Path

import pytest

from vitality_observer.config import ObserverConfig, load_observer_config


def test_defaults_with_no_variables():
    c = load_observer_config({})
    assert c == ObserverConfig()
    assert c.captures_dir == Path("/captures")
    assert c.model_dir == Path("/model")
    assert c.state_dir == Path("/state")
    assert (c.batch_size, c.context_size, c.call_timeout_seconds, c.model) == (10, 15, 600, None)


def test_reads_every_variable():
    env = {
        "OBSERVER_CAPTURES_DIR": "/c",
        "OBSERVER_MODEL_DIR": "/m",
        "OBSERVER_STATE_DIR": "/s",
        "OBSERVER_BATCH_SIZE": "2",
        "OBSERVER_CONTEXT_SIZE": "4",
        "OBSERVER_CALL_TIMEOUT_SECONDS": "12.5",
        "OBSERVER_MODEL": "sonnet",
    }
    assert load_observer_config(env) == ObserverConfig(Path("/c"), Path("/m"), Path("/s"), 2, 4, 12.5, "sonnet")


def test_empty_model_means_claude_codes_default():
    assert load_observer_config({"OBSERVER_MODEL": ""}).model is None


@pytest.mark.parametrize("name,value", [
    ("OBSERVER_BATCH_SIZE", "0"),
    ("OBSERVER_BATCH_SIZE", "x"),
    ("OBSERVER_BATCH_SIZE", "2.5"),
    ("OBSERVER_CONTEXT_SIZE", "-1"),
    ("OBSERVER_CALL_TIMEOUT_SECONDS", "inf"),
    ("OBSERVER_CALL_TIMEOUT_SECONDS", "nan"),
    ("OBSERVER_CALL_TIMEOUT_SECONDS", "0"),
    ("OBSERVER_CALL_TIMEOUT_SECONDS", "-1.5"),
    ("OBSERVER_CALL_TIMEOUT_SECONDS", "soon"),
])
def test_bad_values_name_the_variable(name, value):
    with pytest.raises(ValueError, match=name):
        load_observer_config({name: value})


@pytest.mark.parametrize("name", ["OBSERVER_CAPTURES_DIR", "OBSERVER_MODEL_DIR", "OBSERVER_STATE_DIR"])
def test_empty_directory_is_refused(name):
    with pytest.raises(ValueError, match=name):
        load_observer_config({name: ""})


@pytest.mark.parametrize("name,default", [
    ("OBSERVER_BATCH_SIZE", 10),
    ("OBSERVER_CONTEXT_SIZE", 15),
    ("OBSERVER_CALL_TIMEOUT_SECONDS", 600),
])
def test_empty_number_means_the_default(name, default):
    c = load_observer_config({name: ""})
    assert c == ObserverConfig()
    assert default in (c.batch_size, c.context_size, c.call_timeout_seconds)


def test_unrelated_variables_are_ignored():
    assert load_observer_config({"PATH": "/bin", "SERVER_PORT": "9"}) == ObserverConfig()
