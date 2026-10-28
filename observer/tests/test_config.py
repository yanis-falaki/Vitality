from pathlib import Path

import pytest

from vitality_observer.config import DEFAULT_CONFIG, ObserverConfig, load_observer_config


def test_defaults_when_missing(tmp_path):
    c = load_observer_config(tmp_path / "none.toml")
    assert c == ObserverConfig()
    assert c.batch_size == 10 and c.context_size == 15 and c.model is None
    assert c.captures_dir == Path("/srv/vitality/captures")
    assert c.model_dir == Path("/srv/vitality-model")
    assert c.state_dir == Path("/var/lib/vitality-librarian")
    assert c.call_timeout_seconds == 600
    assert DEFAULT_CONFIG == Path("/etc/vitality/observer.toml")


def test_loads_values(tmp_path):
    p = tmp_path / "o.toml"
    p.write_text('model_dir = "/x"\nbatch_size = 3\nmodel = "sonnet"\n')
    c = load_observer_config(p)
    assert (c.model_dir, c.batch_size, c.model) == (Path("/x"), 3, "sonnet")
    assert c.context_size == 15


def test_loads_every_key(tmp_path):
    p = tmp_path / "o.toml"
    p.write_text(
        'captures_dir = "/c"\nmodel_dir = "/m"\nstate_dir = "/s"\n'
        "batch_size = 2\ncontext_size = 4\ncall_timeout_seconds = 12.5\n"
    )
    c = load_observer_config(p)
    assert c == ObserverConfig(Path("/c"), Path("/m"), Path("/s"), 2, 4, 12.5, None)


@pytest.mark.parametrize("text,key", [
    ("nope = 1", "nope"),
    ('batch_size = "x"', "batch_size"),
    ("batch_size = true", "batch_size"),
    ("batch_size = 2.5", "batch_size"),
    ("context_size = 0", "context_size"),
    ("call_timeout_seconds = 0", "call_timeout_seconds"),
    ("call_timeout_seconds = -1.5", "call_timeout_seconds"),
    ('call_timeout_seconds = "soon"', "call_timeout_seconds"),
    ("call_timeout_seconds = inf", "call_timeout_seconds"),
    ("call_timeout_seconds = nan", "call_timeout_seconds"),
    ("model = 5", "model"),
    ('model = ""', "model"),
    ("captures_dir = 3", "captures_dir"),
    ('model_dir = ""', "model_dir"),
    ("state_dir = []", "state_dir"),
])
def test_rejects_bad_values(tmp_path, text, key):
    p = tmp_path / "o.toml"
    p.write_text(text + "\n")
    with pytest.raises(ValueError, match=key):
        load_observer_config(p)


def test_malformed_toml_is_a_clear_error(tmp_path):
    p = tmp_path / "o.toml"
    p.write_text("batch_size = = 3\n")
    with pytest.raises(ValueError, match=r"o\.toml.*not valid TOML.*line 1"):
        load_observer_config(p)
