from dataclasses import replace
from pathlib import Path

import pytest

from vitality_observer.config import ObserverConfig
from vitality_observer.work import (
    Interaction,
    add_skipped,
    chronological_key,
    context,
    device_of,
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


def observe(cfg: ObserverConfig, name: str, text: str = "seen") -> Path:
    path = cfg.model_dir / "observations" / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


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
    observe(cfg, "laptop/2026-10-25/17-00-00", "")
    assert names(unobserved(cfg, set())) == ["laptop/2026-10-25/17-00-00"]


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


def before(name: str) -> Interaction:
    return Interaction(name=name, folder=Path("/unused"), observation=Path("/unused"))


def test_context_last_n_same_device_before(cfg):
    cfg = replace(cfg, context_size=2)
    for t in ["10-00-00", "11-00-00", "12-00-00", "13-00-00"]:
        observe(cfg, f"laptop/2026-10-25/{t}", f"laptop {t}")
    observe(cfg, "desktop/2026-10-25/12-30-00", "desktop")
    observe(cfg, "laptop/2026-10-25/15-00-00", "later")
    observe(cfg, "laptop/2026-10-25/14-00-00", "the interaction itself")
    assert context(cfg, before("laptop/2026-10-25/14-00-00")) == [
        ("laptop/2026-10-25/12-00-00", "laptop 12-00-00"),
        ("laptop/2026-10-25/13-00-00", "laptop 13-00-00"),
    ]


def test_context_crosses_days(cfg):
    observe(cfg, "laptop/2026-10-24/23-00-00", "late yesterday")
    observe(cfg, "laptop/2026-10-25/08-00-00", "early today")
    observe(cfg, "laptop/2026-10-26/08-00-00", "tomorrow")
    assert context(cfg, before("laptop/2026-10-25/09-00-00")) == [
        ("laptop/2026-10-24/23-00-00", "late yesterday"),
        ("laptop/2026-10-25/08-00-00", "early today"),
    ]


def test_context_orders_clash_suffixes(cfg):
    observe(cfg, "laptop/2026-10-25/17-00-00-2", "second")
    observe(cfg, "laptop/2026-10-25/17-00-00", "first")
    observe(cfg, "laptop/2026-10-25/17-00-00-3", "third")
    assert context(cfg, before("laptop/2026-10-25/17-00-00-3")) == [
        ("laptop/2026-10-25/17-00-00", "first"),
        ("laptop/2026-10-25/17-00-00-2", "second"),
    ]


def test_context_skips_empty_observations(cfg):
    cfg = replace(cfg, context_size=2)
    observe(cfg, "laptop/2026-10-25/10-00-00", "a")
    observe(cfg, "laptop/2026-10-25/11-00-00", "b")
    observe(cfg, "laptop/2026-10-25/12-00-00", "")
    assert context(cfg, before("laptop/2026-10-25/13-00-00")) == [
        ("laptop/2026-10-25/10-00-00", "a"),
        ("laptop/2026-10-25/11-00-00", "b"),
    ]


def test_context_empty_when_no_observations(cfg):
    assert context(cfg, before("laptop/2026-10-25/13-00-00")) == []


def test_context_ignores_and_never_reads_other_devices(cfg, monkeypatch):
    cfg = replace(cfg, context_size=2)
    observe(cfg, "laptop/2026-10-25/10-00-00", "mine")
    observe(cfg, "desktop/2026-10-25/12-00-00", "someone else's, and newer")
    read: list[Path] = []
    original = Path.read_text

    def spy(self: Path, *args, **kwargs) -> str:
        read.append(self)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", spy)
    assert context(cfg, before("laptop/2026-10-25/13-00-00")) == [("laptop/2026-10-25/10-00-00", "mine")]
    assert read == [cfg.model_dir / "observations/laptop/2026-10-25/10-00-00.md"]


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


def test_context_size_not_positive_gives_nothing(cfg):
    observe(cfg, "laptop/2026-10-25/10-00-00", "a")
    assert context(replace(cfg, context_size=0), before("laptop/2026-10-25/13-00-00")) == []


def test_context_skips_undecodable_file_and_takes_an_older_one(cfg):
    cfg = replace(cfg, context_size=2)
    observe(cfg, "laptop/2026-10-25/10-00-00", "a")
    observe(cfg, "laptop/2026-10-25/11-00-00", "b")
    bad = observe(cfg, "laptop/2026-10-25/12-00-00")
    bad.write_bytes(b"\xff\xfe\x00bad")
    assert context(cfg, before("laptop/2026-10-25/13-00-00")) == [
        ("laptop/2026-10-25/10-00-00", "a"),
        ("laptop/2026-10-25/11-00-00", "b"),
    ]


def test_utf8_round_trips(cfg, tmp_path):
    observe(cfg, "laptop/2026-10-25/10-00-00", "caf\u00e9 \u2014 \u65e5\u672c")
    assert context(cfg, before("laptop/2026-10-25/13-00-00")) == [("laptop/2026-10-25/10-00-00", "caf\u00e9 \u2014 \u65e5\u672c")]
    path = tmp_path / "skipped.txt"
    add_skipped(path, "laptop/2026-10-25/10-00-00", "\u00e9chec")
    assert read_skipped(path) == {"laptop/2026-10-25/10-00-00": "\u00e9chec"}
