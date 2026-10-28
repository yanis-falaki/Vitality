import asyncio
import logging
from collections.abc import Callable, Sequence
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

    An outcome is "ok" (writes every file), "fail", "oserror" (raises), "limit", ("limit", reset_epoch), or a number
    (succeeds but writes only that many files, oldest first).
    """

    def __init__(self, cfg: ObserverConfig, *outcomes: str | int | tuple[str, float],
                 on_call: Callable[[], None] | None = None):
        self.cfg = cfg
        self.on_call = on_call
        self.outcomes = list(outcomes)
        self.batches: list[list[str]] = []
        self.contexts: list[list[tuple[str, str]]] = []
        self.called = asyncio.Event()

    async def observe(self, batch: Sequence[Interaction], context: Sequence[tuple[str, str]]) -> CallResult:
        self.batches.append([i.name for i in batch])
        self.contexts.append(list(context))
        self.called.set()
        if self.on_call:
            self.on_call()
        outcome = self.outcomes.pop(0) if self.outcomes else "ok"
        match outcome:
            case "fail":
                return result(False, "boom")
            case "oserror":
                raise OSError("disk trouble")
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


async def cancel(task: asyncio.Task) -> None:
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


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


async def test_failed_batch_is_followed_by_a_single_and_counts_nothing(cfg):
    names = captures(cfg, 3)
    observer = FakeObserver(cfg, "fail", "ok", "ok")
    loop = ObserveLoop(cfg, observer, max_attempts=1)
    assert await loop.step() == "failed"
    assert read_skipped(cfg.state_dir / "skipped.txt") == {}    # even with one attempt allowed
    assert await loop.step() == "observed"
    assert await loop.step() == "observed"
    assert observer.batches == [names, [names[0]], names[1:]]   # full batches resume after success


async def test_only_the_interaction_failing_alone_is_skipped(cfg):
    bad, good1, good2 = captures(cfg, 3)
    observer = FakeObserver(cfg, "fail", "fail", "fail", "fail")
    loop = ObserveLoop(cfg, observer)
    for _ in range(4):
        assert await loop.step() == "failed"
    assert observer.batches == [[bad, good1, good2]] + [[bad]] * 3
    assert read_skipped(cfg.state_dir / "skipped.txt") == {bad: "boom"}
    assert await loop.step() == "observed"
    assert observer.batches[-1] == [good1]      # still one at a time until a call succeeds


async def test_attempts_stop_counting_once_backoff_reaches_max(cfg):
    first, second, third = captures(cfg, 3)
    sleeps = Sleeps(12)
    loop = ObserveLoop(cfg, FakeObserver(cfg, *["fail"] * 12), sleep=sleeps)
    with pytest.raises(StopRun):
        await loop.run()
    assert sleeps.seconds == [5, 10, 20, 40, 80, 160] + [300] * 6
    # Failure 1 was a batch of 3 (no count); failures 2-4 skip `first`; 5 and 6 count against
    # `second`; from failure 7 the backoff is 300 and nothing counts, however long it lasts.
    assert read_skipped(cfg.state_dir / "skipped.txt") == {first: "boom"}
    assert third not in read_skipped(cfg.state_dir / "skipped.txt")


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


async def test_usage_limit_wait_has_a_floor(cfg):
    captures(cfg, 1)
    sleeps = Sleeps(2)
    observer = FakeObserver(cfg, ("limit", 900.0), ("limit", 1010.0))     # reset passed; reset in 10 s
    loop = ObserveLoop(cfg, observer, sleep=sleeps, clock=lambda: 1000.0)
    with pytest.raises(StopRun):
        await loop.run()
    assert sleeps.seconds == [60, 60]


async def test_error_in_a_round_is_retried_and_logged_once(cfg, caplog):
    captures(cfg, 1)
    sleeps = Sleeps(3)
    loop = ObserveLoop(cfg, FakeObserver(cfg, "oserror", "oserror", "oserror"), sleep=sleeps)
    with caplog.at_level(logging.INFO, logger="vitality_observer"), pytest.raises(StopRun):
        await loop.run()
    assert sleeps.seconds == [5, 10, 20]
    assert len([r for r in caplog.records if r.exc_info]) == 1


async def test_idle_resets_backoff_and_logging(cfg, caplog):
    capture(cfg, "laptop/2026-10-25/10-00-00")
    sleeps = Sleeps(3)
    observer = FakeObserver(cfg, "fail", "fail", "fail")
    loop = ObserveLoop(cfg, observer, sleep=sleeps, rescan_seconds=0.02, max_attempts=2)
    with caplog.at_level(logging.INFO, logger="vitality_observer"):
        task = asyncio.create_task(loop.run())
        await asyncio.sleep(0.2)        # failed twice, skipped, now idle
        assert sleeps.seconds == [5, 10]
        capture(cfg, "laptop/2026-10-25/10-00-01")
        with pytest.raises(StopRun):
            await asyncio.wait_for(task, 2)
    assert sleeps.seconds == [5, 10, 5]
    assert len([r for r in caplog.records if "failed, retrying" in r.getMessage()]) == 2


async def test_success_logs_recovery_only_when_something_was_observed(cfg, caplog):
    captures(cfg, 1)
    observer = FakeObserver(cfg, "fail", 0, "ok")
    loop = ObserveLoop(cfg, observer, max_attempts=100)
    with caplog.at_level(logging.INFO, logger="vitality_observer"):
        for _ in range(3):
            await loop.step()
    works = [i for i, r in enumerate(caplog.records) if "works again" in r.getMessage()]
    assert len(works) == 1
    assert "observed 1" in caplog.records[works[0] + 1].getMessage()


async def test_attempt_is_forgotten_once_observed(cfg):
    _, b = captures(cfg, 2)
    observer = FakeObserver(cfg, 1, "ok", "fail")
    loop = ObserveLoop(cfg, observer, max_attempts=2)
    await loop.step()       # first written, `b` missing: one attempt
    await loop.step()       # `b` written
    (cfg.model_dir / "observations" / f"{b}.md").unlink()
    assert await loop.step() == "failed"        # `b` fails alone: its first attempt, not its second
    assert read_skipped(cfg.state_dir / "skipped.txt") == {}


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
        await cancel(task)


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
        await cancel(task)


async def test_interaction_arriving_during_a_call_is_picked_up_promptly(cfg):
    first, = captures(cfg, 1)
    second = "laptop/2026-10-25/11-00-00"
    arrived = []

    def arrive():
        if not arrived:
            arrived.append(second)
            capture(cfg, second)
            loop.wake()

    observer = FakeObserver(cfg, on_call=arrive)
    loop = ObserveLoop(cfg, observer, rescan_seconds=30)
    task = asyncio.create_task(loop.run())

    async def two_calls():
        while len(observer.batches) < 2:
            await asyncio.sleep(0.01)
    try:
        await asyncio.wait_for(two_calls(), 1)
        assert observer.batches == [[first], [second]]
    finally:
        await cancel(task)
