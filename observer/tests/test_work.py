from pathlib import Path

import pytest

from vitality_observer.config import ObserverConfig
from vitality_observer.work import (
    Interaction,
    add_skipped,
    chronological_key,
    device_of,
    interactions,
    read_skipped,
    sort_key,
    unobserved,
)


@pytest.fixture
def cfg(tmp_path: Path) -> ObserverConfig:
    return ObserverConfig(
        captures_dir=tmp_path / "captures",
        model_dir=tmp_path / "model",
        state_dir=tmp_path / "state",
    )


def capture(cfg: ObserverConfig, name: str, *, with_json: bool = True) -> None:
    folder = cfg.captures_dir / name
    folder.mkdir(parents=True)
    (folder / "0000.webp").write_bytes(b"x")
    if with_json:
        (folder / "interaction.json").write_text("{}")


def observe(cfg: ObserverConfig, name: str, text: str = "seen", operations: str | None = None) -> None:
    """Write both evidence files of `name`; `operations` defaults to `text`."""
    for directory, content in (("observations", text), ("operations", text if operations is None else operations)):
        path = cfg.model_dir / directory / f"{name}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def names(found: list[Interaction]) -> list[str]:
    return [i.name for i in found]


def test_device_of_is_the_first_part():
    assert device_of("laptop/2026-10-25/17-00-00-2") == "laptop"


def test_sort_key_splits_name_and_defaults_suffix_to_one():
    assert sort_key("laptop/2026-10-25/17-00-00") == ("laptop", "2026-10-25", "17-00-00", 1)
    assert sort_key("laptop/2026-10-25/17-00-00-2") == ("laptop", "2026-10-25", "17-00-00", 2)
    assert sort_key("laptop/2026-10-25/17-00-00-10") > sort_key("laptop/2026-10-25/17-00-00-9")


def test_chronological_key_puts_day_and_time_before_device():
    assert chronological_key("laptop/2026-10-25/17-00-00") == ("2026-10-25", "17-00-00", 1, "laptop")
    assert chronological_key("laptop/2026-10-25/17-00-00-2") == ("2026-10-25", "17-00-00", 2, "laptop")
    assert chronological_key("phone/2026-10-25/09-00-00") < chronological_key("desktop/2026-10-25/10-00-00")


def test_unobserved_returns_interactions_with_paths(cfg):
    capture(cfg, "laptop/2026-10-25/17-00-00")
    [found] = unobserved(cfg, set())
    assert found == Interaction(
        name="laptop/2026-10-25/17-00-00",
        folder=cfg.captures_dir / "laptop/2026-10-25/17-00-00",
        observation=cfg.model_dir / "observations/laptop/2026-10-25/17-00-00.md",
        operations=cfg.model_dir / "operations/laptop/2026-10-25/17-00-00.md",
    )


def test_unobserved_when_captures_dir_missing(cfg):
    assert unobserved(cfg, set()) == []


def test_unobserved_needs_interaction_json(cfg):
    capture(cfg, "laptop/2026-10-25/17-00-00", with_json=False)
    capture(cfg, "laptop/2026-10-25/17-00-01")
    assert names(unobserved(cfg, set())) == ["laptop/2026-10-25/17-00-01"]


def test_observed_is_excluded(cfg):
    capture(cfg, "laptop/2026-10-25/17-00-00")
    capture(cfg, "laptop/2026-10-25/17-00-01")
    observe(cfg, "laptop/2026-10-25/17-00-00")
    assert names(unobserved(cfg, set())) == ["laptop/2026-10-25/17-00-01"]


def test_empty_observation_file_is_unobserved(cfg):
    capture(cfg, "laptop/2026-10-25/17-00-00")
    observe(cfg, "laptop/2026-10-25/17-00-00", "", operations="ops")
    assert names(unobserved(cfg, set())) == ["laptop/2026-10-25/17-00-00"]


def test_observed_needs_both_files(cfg):
    for name in ("laptop/2026-10-25/17-00-00", "laptop/2026-10-25/17-00-01", "laptop/2026-10-25/17-00-02",
                 "laptop/2026-10-25/17-00-03"):
        capture(cfg, name)
    # observation only: still unobserved
    only_observation = cfg.model_dir / "observations/laptop/2026-10-25/17-00-00.md"
    only_observation.parent.mkdir(parents=True, exist_ok=True)
    only_observation.write_text("seen")
    # both non-empty: observed
    observe(cfg, "laptop/2026-10-25/17-00-01")
    # empty operations file: unobserved
    observe(cfg, "laptop/2026-10-25/17-00-02", "seen", operations="")
    # operations only: unobserved
    only_operations = cfg.model_dir / "operations/laptop/2026-10-25/17-00-03.md"
    only_operations.parent.mkdir(parents=True, exist_ok=True)
    only_operations.write_text("ops")
    assert names(unobserved(cfg, set())) == [
        "laptop/2026-10-25/17-00-00",
        "laptop/2026-10-25/17-00-02",
        "laptop/2026-10-25/17-00-03",
    ]


def test_oldest_first_across_devices_and_days(cfg):
    for name in [
        "desktop/2026-10-26/08-00-00",
        "laptop/2026-10-25/17-00-00",
        "phone/2026-10-25/09-00-00",
        "laptop/2026-10-25/17-00-00-2",
        "desktop/2026-10-25/17-00-00",
    ]:
        capture(cfg, name)
    assert names(unobserved(cfg, set())) == [
        "phone/2026-10-25/09-00-00",
        "desktop/2026-10-25/17-00-00",
        "laptop/2026-10-25/17-00-00",
        "laptop/2026-10-25/17-00-00-2",
        "desktop/2026-10-26/08-00-00",
    ]


def test_clash_suffix_names(cfg):
    ordered = [
        "laptop/2026-10-25/17-00-00",
        "laptop/2026-10-25/17-00-00-2",
        "laptop/2026-10-25/17-00-01",
    ]
    for name in reversed(ordered):
        capture(cfg, name)
    found = unobserved(cfg, set())
    assert names(found) == ordered
    assert found[1].observation == cfg.model_dir / "observations/laptop/2026-10-25/17-00-00-2.md"
    assert found[1].operations == cfg.model_dir / "operations/laptop/2026-10-25/17-00-00-2.md"


def test_clash_suffix_sorts_numerically(cfg):
    ordered = ["laptop/2026-10-25/17-00-00-2", "laptop/2026-10-25/17-00-00-10"]
    for name in reversed(ordered):
        capture(cfg, name)
    assert names(unobserved(cfg, set())) == ordered


def test_skipped_are_excluded(cfg):
    capture(cfg, "laptop/2026-10-25/17-00-00")
    capture(cfg, "laptop/2026-10-25/17-00-01")
    assert names(unobserved(cfg, {"laptop/2026-10-25/17-00-00"})) == ["laptop/2026-10-25/17-00-01"]


def test_skipped_accepts_a_dict(cfg):
    capture(cfg, "laptop/2026-10-25/17-00-00")
    assert unobserved(cfg, {"laptop/2026-10-25/17-00-00": "why"}) == []


def test_ignores_other_depths(cfg):
    (cfg.captures_dir / "laptop/2026-10-25").mkdir(parents=True)
    (cfg.captures_dir / "stray.json").write_text("{}")
    (cfg.captures_dir / "laptop/stray.json").write_text("{}")
    (cfg.captures_dir / "laptop/2026-10-25/17-00-00.json").write_text("{}")
    (cfg.captures_dir / "interaction.json").write_text("{}")
    (cfg.captures_dir / "laptop/interaction.json").write_text("{}")
    (cfg.captures_dir / "laptop/2026-10-25/interaction.json").write_text("{}")
    capture(cfg, "laptop/2026-10-25/17-00-00/deeper")
    (cfg.captures_dir / "laptop/2026-10-25/17-00-00/deeper/interaction.json").write_text("{}")
    assert unobserved(cfg, set()) == []


def test_skipped_round_trip(tmp_path):
    path = tmp_path / "state" / "skipped.txt"
    assert read_skipped(path) == {}
    add_skipped(path, "laptop/2026-10-25/17-00-00", "timed out\twith\ttabs")
    add_skipped(path, "laptop/2026-10-25/17-00-01", "line one\nline two")
    assert read_skipped(path) == {
        "laptop/2026-10-25/17-00-00": "timed out with tabs",
        "laptop/2026-10-25/17-00-01": "line one line two",
    }
    assert path.read_text().count("\n") == 2


def test_utf8_round_trips(tmp_path):
    path = tmp_path / "skipped.txt"
    add_skipped(path, "laptop/2026-10-25/10-00-00", "\u00e9chec")
    assert read_skipped(path) == {"laptop/2026-10-25/10-00-00": "\u00e9chec"}


def test_interactions_lists_every_interaction_chronologically(cfg):
    for name in ("phone/2026-10-25/09-00-00", "laptop/2026-10-25/17-00-00-2", "laptop/2026-10-25/17-00-00",
                 "laptop/2026-10-24/23-00-00"):
        capture(cfg, name)
    capture(cfg, "laptop/2026-10-25/18-00-00", with_json=False)     # not an interaction
    observe(cfg, "laptop/2026-10-25/17-00-00")                      # observed ones are listed too
    found = interactions(cfg)
    assert names(found) == ["laptop/2026-10-24/23-00-00", "phone/2026-10-25/09-00-00",
                            "laptop/2026-10-25/17-00-00", "laptop/2026-10-25/17-00-00-2"]
    assert found[2].folder == cfg.captures_dir / "laptop/2026-10-25/17-00-00"
    assert found[2].observation == cfg.model_dir / "observations/laptop/2026-10-25/17-00-00.md"
    assert found[2].operations == cfg.model_dir / "operations/laptop/2026-10-25/17-00-00.md"


def test_interactions_of_a_missing_captures_dir_is_empty(cfg):
    assert interactions(cfg) == []
