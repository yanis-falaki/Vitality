import asyncio
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

import pytest

from vitality_observer.claude import CallResult
from vitality_observer.config import ObserverConfig
from vitality_observer.loop import ObserveLoop
from vitality_observer.work import Interaction, read_skipped


@pytest.fixture
def cfg(tmp_path: Path) -> ObserverConfig:
    return ObserverConfig(
        captures_dir=tmp_path / "captures",
        model_dir=tmp_path / "model",
        state_dir=tmp_path / "state",
    )


def capture(cfg: ObserverConfig, name: str) -> None:
    folder = cfg.captures_dir / name
    folder.mkdir(parents=True)
    (folder / "interaction.json").write_text("{}")


def observe(cfg: ObserverConfig, name: str, text: str = "seen") -> None:
    path = cfg.model_dir / "observations" / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def captures(cfg: ObserverConfig, count: int, device: str = "laptop") -> list[str]:
    names = [f"{device}/2026-10-25/10-00-{n:02d}" for n in range(count)]
    for name in names:
        capture(cfg, name)
    return names


def result(ok: bool, detail: str = "", limited: bool = False, reset: float | None = None) -> CallResult:
    return CallResult(ok=ok, usage_limit_reset=reset, usage_limited=limited, detail=detail, record={})


class FakeObserver:
    """An Observer that plays back `outcomes`, then succeeds; remembers every call.

    An outcome is "ok" (writes every file), "fail", "limit", ("limit", reset_epoch), or a number
    (succeeds but writes only that many files, oldest first).
    """

    def __init__(self, cfg: ObserverConfig, *outcomes: str | int | tuple[str, float]):
        self.cfg = cfg
        self.outcomes = list(outcomes)
        self.batches: list[list[str]] = []
        self.contexts: list[list[tuple[str, str]]] = []
        self.called = asyncio.Event()

    async def observe(self, batch: Sequence[Interaction], context: Sequence[tuple[str, str]]) -> CallResult:
        self.batches.append([i.name for i in batch])
        self.contexts.append(list(context))
        self.called.set()
        outcome = self.outcomes.pop(0) if self.outcomes else "ok"
        match outcome:
            case "fail":
                return result(False, "boom")
            case "limit":
                return result(False, "usage limit", limited=True)
            case ("limit", reset):
                return result(False, "usage limit", limited=True, reset=reset)
            case int(count):
                names = [i.name for i in batch][:count]
            case _:
                names = [i.name for i in batch]
        for name in names:
            observe(self.cfg, name)
        return result(True)


class StopRun(Exception):
    pass


class Sleeps:
    """A sleep that records its argument and ends `run()` after `limit` calls."""

    def __init__(self, limit: int):
        self.limit = limit
        self.seconds: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.seconds.append(seconds)
        if len(self.seconds) >= self.limit:
            raise StopRun


async def test_idle_single_interaction_called_alone(cfg):
    observer = FakeObserver(cfg)
    loop = ObserveLoop(cfg, observer)
    assert await loop.step() == "idle"
    assert observer.batches == []

    (name,) = captures(cfg, 1)
    assert await loop.step() == "observed"
    assert observer.batches == [[name]]
    assert await loop.step() == "idle"


async def test_batch_capped_at_batch_size(cfg):
    names = captures(cfg, 13)
    observer = FakeObserver(cfg)
    loop = ObserveLoop(cfg, observer)
    assert await loop.step() == "observed"
    assert await loop.step() == "observed"
    assert observer.batches == [names[:10], names[10:]]


async def test_batch_stops_at_device_change(cfg):
    for name in ("laptop/2026-10-25/10-00-00", "desktop/2026-10-25/10-00-01",
                 "laptop/2026-10-25/10-00-02", "laptop/2026-10-25/10-00-03"):
        capture(cfg, name)
    observer = FakeObserver(cfg)
    loop = ObserveLoop(cfg, observer)
    for _ in range(3):
        await loop.step()
    assert observer.batches == [
        ["laptop/2026-10-25/10-00-00"],
        ["desktop/2026-10-25/10-00-01"],
        ["laptop/2026-10-25/10-00-02", "laptop/2026-10-25/10-00-03"],
    ]


async def test_context_passed_is_before_first_of_batch(cfg):
    observe(cfg, "laptop/2026-10-25/09-00-00", "earlier")
    observe(cfg, "desktop/2026-10-25/09-30-00", "other device")
    first, second = captures(cfg, 2)
    observer = FakeObserver(cfg)
    await ObserveLoop(cfg, observer).step()
    assert observer.batches == [[first, second]]
    assert observer.contexts == [[("laptop/2026-10-25/09-00-00", "earlier")]]


async def test_missing_files_retried_next_round(cfg):
    names = captures(cfg, 3)
    observer = FakeObserver(cfg, 2)
    loop = ObserveLoop(cfg, observer)
    assert await loop.step() == "observed"
    assert await loop.step() == "observed"
    assert observer.batches == [names, [names[2]]]
    assert await loop.step() == "idle"


async def test_missing_file_is_skipped_after_max_attempts(cfg):
    (name,) = captures(cfg, 1)
    observer = FakeObserver(cfg, 0, 0, 0)
    loop = ObserveLoop(cfg, observer)
    while await loop.step() != "idle":
        pass
    assert len(observer.batches) == 3
    assert read_skipped(cfg.state_dir / "skipped.txt") == {name: "no observation written"}


async def test_three_failures_skip(cfg):
    bad, good = captures(cfg, 2)
    observer = FakeObserver(cfg, "fail", "fail", "fail")
    loop = ObserveLoop(replace(cfg, batch_size=1), observer)
    assert [await loop.step() for _ in range(3)] == ["failed"] * 3
    assert read_skipped(cfg.state_dir / "skipped.txt") == {bad: "boom"}

    assert await loop.step() == "observed"      # the next interaction
    assert observer.batches == [[bad]] * 3 + [[good]]
    assert await loop.step() == "idle"          # the skipped one is not offered again


async def test_failure_attempts_cover_the_whole_batch(cfg):
    names = captures(cfg, 2)
    loop = ObserveLoop(cfg, FakeObserver(cfg, "fail", "fail", "fail"))
    for _ in range(3):
        await loop.step()
    assert set(read_skipped(cfg.state_dir / "skipped.txt")) == set(names)


async def test_failure_backoff_doubles(cfg):
    captures(cfg, 1)
    sleeps = Sleeps(8)
    loop = ObserveLoop(cfg, FakeObserver(cfg, *["fail"] * 8), sleep=sleeps, max_attempts=100)
    with pytest.raises(StopRun):
        await loop.run()
    assert sleeps.seconds == [5, 10, 20, 40, 80, 160, 300, 300]


async def test_success_resets_backoff(cfg):
    captures(cfg, 2)
    sleeps = Sleeps(3)
    observer = FakeObserver(cfg, "fail", "fail", "ok", "fail")
    loop = ObserveLoop(replace(cfg, batch_size=1), observer, sleep=sleeps, max_attempts=100)
    with pytest.raises(StopRun):
        await loop.run()
    assert sleeps.seconds == [5, 10, 5]


async def test_usage_limit_waits_until_reset(cfg):
    captures(cfg, 1)
    sleeps = Sleeps(1)
    observer = FakeObserver(cfg, ("limit", 1120.0))
    loop = ObserveLoop(cfg, observer, sleep=sleeps, clock=lambda: 1000.0)
    with pytest.raises(StopRun):
        await loop.run()
    assert sleeps.seconds == [120]


async def test_usage_limit_without_reset_waits_default(cfg):
    captures(cfg, 1)
    sleeps = Sleeps(1)
    loop = ObserveLoop(cfg, FakeObserver(cfg, "limit"), sleep=sleeps)
    with pytest.raises(StopRun):
        await loop.run()
    assert sleeps.seconds == [1800]


async def test_usage_limit_does_not_count_as_attempt_or_backoff(cfg):
    (name,) = captures(cfg, 1)
    sleeps = Sleeps(4)
    observer = FakeObserver(cfg, "limit", "limit", "limit", "fail", "ok")
    loop = ObserveLoop(cfg, observer, sleep=sleeps, clock=lambda: 1000.0, max_attempts=2)
    with pytest.raises(StopRun):
        await loop.run()
    # three limits (no attempts, backoff untouched), then one real failure sleeping the initial backoff
    assert sleeps.seconds == [1800, 1800, 1800, 5]
    assert read_skipped(cfg.state_dir / "skipped.txt") == {}
    assert await loop.step() == "observed"
    assert observer.batches == [[name]] * 5


async def test_usage_limit_with_past_reset_waits_zero(cfg):
    captures(cfg, 1)
    sleeps = Sleeps(1)
    loop = ObserveLoop(cfg, FakeObserver(cfg, ("limit", 900.0)), sleep=sleeps, clock=lambda: 1000.0)
    with pytest.raises(StopRun):
        await loop.run()
    assert sleeps.seconds == [0]


async def test_wake_starts_promptly_when_idle(cfg):
    observer = FakeObserver(cfg)
    loop = ObserveLoop(cfg, observer, rescan_seconds=30)
    task = asyncio.create_task(loop.run())
    try:
        await asyncio.sleep(0.05)       # let run() find nothing and start waiting
        (name,) = captures(cfg, 1)
        loop.wake()
        await asyncio.wait_for(observer.called.wait(), 1)
        assert observer.batches == [[name]]
    finally:
        task.cancel()


async def test_rescan_without_wake(cfg):
    observer = FakeObserver(cfg)
    loop = ObserveLoop(cfg, observer, rescan_seconds=0.05)
    task = asyncio.create_task(loop.run())
    try:
        await asyncio.sleep(0.02)
        (name,) = captures(cfg, 1)
        await asyncio.wait_for(observer.called.wait(), 1)
        assert observer.batches == [[name]]
    finally:
        task.cancel()
