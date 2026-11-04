import pytest
from vitality_modeler.config import ModelerConfig, load_modeler_config


def test_config_defaults_and_env():
    assert load_modeler_config({}) == ModelerConfig()
    assert load_modeler_config({"MODELER_MODEL": " "}) == ModelerConfig()
    config = load_modeler_config({
        "MODELER_MODEL": "m", "MODELER_EFFORT": "max",
        "MODELER_MAX_OPERATIONS": "20", "MODELER_RUN_TIMEOUT_SECONDS": "1.5",
    })
    assert config == ModelerConfig("m", "max", 20, 1.5)


@pytest.mark.parametrize("name, value", [
    ("MODELER_EFFORT", "huge"),
    ("MODELER_MAX_OPERATIONS", "0"),
    ("MODELER_MAX_OPERATIONS", "2.5"),
    ("MODELER_RUN_TIMEOUT_SECONDS", "nan"),
    ("MODELER_RUN_TIMEOUT_SECONDS", "-1"),
    ("MODELER_RUN_TIMEOUT_SECONDS", "inf"),
    ("MODELER_RUN_TIMEOUT_SECONDS", "soon"),
])
def test_config_rejects(name: str, value: str):
    with pytest.raises(ValueError, match=name):
        load_modeler_config({name: value})
