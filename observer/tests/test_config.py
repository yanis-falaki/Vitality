from pathlib import Path

import pytest

from vitality_observer.config import ObserverConfig, load_observer_config


def test_defaults_with_no_variables():
    c = load_observer_config({})
    assert c == ObserverConfig()
    assert c.captures_dir == Path("/captures")
    assert c.model_dir == Path("/model")
    assert c.state_dir == Path("/state")
    assert (c.mode, c.parallel, c.model, c.effort, c.call_timeout_seconds) == ("direct", 4, "claude-sonnet-5-5", "low", 600)


def test_reads_every_variable():
    env = {
        "OBSERVER_CAPTURES_DIR": "/c",
        "OBSERVER_MODEL_DIR": "/m",
        "OBSERVER_STATE_DIR": "/s",
        "OBSERVER_MODE": "batch",
        "OBSERVER_PARALLEL": "2",
        "OBSERVER_MODEL": "claude-opus-4-5",
        "OBSERVER_EFFORT": "high",
        "OBSERVER_CALL_TIMEOUT_SECONDS": "12.5",
    }
    assert load_observer_config(env) == ObserverConfig(
        Path("/c"), Path("/m"), Path("/s"), "batch", 2, "claude-opus-4-5", "high", 12.5
    )


def test_mode_batch():
    assert load_observer_config({"OBSERVER_MODE": "batch"}).mode == "batch"


@pytest.mark.parametrize("effort", ["low", "medium", "high", "xhigh", "max"])
def test_every_effort_is_accepted(effort):
    assert load_observer_config({"OBSERVER_EFFORT": effort}).effort == effort


@pytest.mark.parametrize("name,value", [
    ("OBSERVER_MODE", "fast"),
    ("OBSERVER_MODE", "Direct"),
    ("OBSERVER_PARALLEL", "0"),
    ("OBSERVER_PARALLEL", "-3"),
    ("OBSERVER_PARALLEL", "x"),
    ("OBSERVER_PARALLEL", "2.5"),
    ("OBSERVER_EFFORT", "huge"),
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


@pytest.mark.parametrize("name", [
    "OBSERVER_MODE",
    "OBSERVER_PARALLEL",
    "OBSERVER_MODEL",
    "OBSERVER_EFFORT",
    "OBSERVER_CALL_TIMEOUT_SECONDS",
])
def test_empty_or_blank_means_the_default(name):
    assert load_observer_config({name: ""}) == ObserverConfig()
    assert load_observer_config({name: "  "}) == ObserverConfig()


def test_unrelated_variables_are_ignored():
    assert load_observer_config({"PATH": "/bin", "SERVER_PORT": "9"}) == ObserverConfig()
