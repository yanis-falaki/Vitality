import asyncio
import contextlib
import logging
from collections.abc import Callable, Collection, Sequence
from dataclasses import replace
from pathlib import Path

import pytest

from vitality_observer import loop as loop_module
from vitality_observer.config import ObserverConfig
from vitality_observer.loop import BATCH_LIMIT, ObserveLoop
from vitality_observer.provider.interface import Outcome
from vitality_observer.work import Interaction, add_skipped, read_skipped, unobserved


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


def evidence(cfg: ObserverConfig, kind: str, name: str) -> Path:
    """The `observations` or `operations` file of `name`."""
    return cfg.model_dir / kind / f"{name}.md"


def write(cfg: ObserverConfig, name: str, text: str = "seen", kinds: Sequence[str] = ("observations", "operations")) -> None:
    for kind in kinds:
        path = evidence(cfg, kind, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def captures(cfg: ObserverConfig, count: int, device: str = "laptop") -> list[str]:
    names = [f"{device}/2026-10-25/10-{n // 60:02d}-{n % 60:02d}" for n in range(count)]
    for name in names:
        capture(cfg, name)
    return names


Behaviour = str | tuple[str, float]


class FakeObserver:
    """An Observer whose outcome is chosen per interaction; remembers every call.

    A behaviour is "ok" (writes both files, with the text `call <n>`), "fail" (reached the model,
    failed with detail "boom", writes nothing), "fail:<detail>" (the same with that detail),
    "unreached" (failed without reaching the model, detail "no api"), "half" (writes only the
    observation file, fails without reaching the model), "lie" (ok, but writes nothing),
    "fail+files" (reached the model and failed with "boom", yet writes both files), or
    ("wait", seconds) (out of credits: that `wait_seconds`, not reached, writes nothing).

    Each call takes the next of `rounds`: a behaviour for the whole batch, or a dict of
    behaviours by name (the rest "ok"). Once they run out, interactions in `poison` "fail"
    and the others are "ok".
    """

    def __init__(self, cfg: ObserverConfig, *rounds: Behaviour | dict[str, Behaviour],
                 poison: Collection[str] = (), on_call: Callable[[], None] | None = None):
        self.cfg = cfg
        self.rounds = list(rounds)
        self.poison = poison
        self.on_call = on_call
        self.batches: list[list[str]] = []
        self.called = asyncio.Event()

    async def observe(self, batch: Sequence[Interaction]) -> list[Outcome]:
        self.batches.append([i.name for i in batch])
        self.called.set()
        if self.on_call:
            self.on_call()
        plan = self.rounds.pop(0) if self.rounds else {name: "fail" for name in self.poison}
        return [self._one(i.name, plan.get(i.name, "ok") if isinstance(plan, dict) else plan) for i in batch]

    def _one(self, name: str, behaviour: Behaviour) -> Outcome:
        text = f"call {len(self.batches)}"
        match behaviour:
            case "ok":
                write(self.cfg, name, text)
                return outcome(name, ok=True)
            case "fail":
                return outcome(name, detail="boom")
            case str(failure) if failure.startswith("fail:"):
                return outcome(name, detail=failure.removeprefix("fail:"))
            case "unreached":
                return outcome(name, reached=False, detail="no api")
            case "half":
                write(self.cfg, name, text, kinds=["observations"])
                return outcome(name, reached=False, detail="half written")
            case "lie":
                return outcome(name, ok=True)
            case "fail+files":
                write(self.cfg, name, text)
                return outcome(name, detail="boom")
            case ("wait", float(seconds) | int(seconds)):
                return outcome(name, reached=False, detail="out of API credits", wait=seconds)
        raise AssertionError(f"unknown behaviour {behaviour!r}")


def outcome(name: str, ok: bool = False, reached: bool = True, detail: str = "",
            wait: float | None = None) -> Outcome:
    return Outcome(name=name, ok=ok, reached_model=reached, detail=detail, wait_seconds=wait, record={})


async def cancel(task: asyncio.Task) -> None:
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


class StopRun(Exception):
    pass


class Sleeps:
    """A sleep that records its argument, lets other tasks run, and ends `run()` after `limit` calls.

    `on_sleep(n)` is called with the number of sleeps so far, before the sleep returns.
    """

    def __init__(self, limit: int, on_sleep: Callable[[int], None] | None = None):
        self.limit = limit
        self.on_sleep = on_sleep
        self.seconds: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.seconds.append(seconds)
        if self.on_sleep:
            self.on_sleep(len(self.seconds))
        if len(self.seconds) >= self.limit:
            raise StopRun
        await asyncio.sleep(0)


async def run_to_stop(loop: ObserveLoop, seconds: float = 5) -> None:
    """Run `loop.run()` until its `Sleeps` ends it; fail, rather than hang, if that takes over `seconds`."""
    with pytest.raises(StopRun):
        await asyncio.wait_for(loop.run(), seconds)


async def run_until(loop: ObserveLoop, done: Callable[[], bool], seconds: float = 5) -> None:
    """Run `loop.run()` until `done()`; fail, rather than hang, if it takes over `seconds` or `run()` stops."""
    task = asyncio.create_task(loop.run())

    async def wait_done() -> None:
        while not done():
            if task.done():
                pytest.fail(f"run() stopped before the end: {task.exception()!r}")
            await asyncio.sleep(0.001)
    try:
        await asyncio.wait_for(wait_done(), seconds)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, StopRun):
            await task


def settled(cfg: ObserverConfig) -> Callable[[], bool]:
    """Whether every interaction is observed or skipped."""
    return lambda: not unobserved(cfg, read_skipped(cfg.state_dir / "skipped.txt"))


def skipped(cfg: ObserverConfig) -> dict[str, str]:
    return read_skipped(cfg.state_dir / "skipped.txt")


def is_observed(cfg: ObserverConfig, name: str) -> bool:
    return all(evidence(cfg, kind, name).is_file() for kind in ("observations", "operations"))


# -- rule 1: round ------------------------------------------------------------------------------


async def test_idle_then_a_single_interaction(cfg):
    observer = FakeObserver(cfg)
    loop = ObserveLoop(cfg, observer)
    assert await loop.step() == "idle"
    assert observer.batches == []

    (name,) = captures(cfg, 1)
    assert await loop.step() == "observed"
    assert observer.batches == [[name]]
    assert await loop.step() == "idle"


async def test_round_takes_oldest_across_devices(cfg):
    names = ["laptop/2026-10-25/10-00-00", "desktop/2026-10-25/10-00-01", "phone/2026-10-25/10-00-02",
             "laptop/2026-10-25/10-00-03", "desktop/2026-10-25/10-00-04", "laptop/2026-10-25/10-00-05"]
    for name in reversed(names):
        capture(cfg, name)
    observer = FakeObserver(cfg)
    loop = ObserveLoop(cfg, observer)       # direct mode, parallel 4
    assert [await loop.step() for _ in range(3)] == ["observed", "observed", "idle"]
    assert observer.batches == [names[:4], names[4:]]


async def test_direct_round_size_is_parallel(cfg):
    names = captures(cfg, 5)
    observer = FakeObserver(cfg)
    loop = ObserveLoop(replace(cfg, parallel=2), observer)
    for _ in range(3):
        await loop.step()
    assert observer.batches == [names[:2], names[2:4], names[4:]]


async def test_batch_round_size(cfg):
    assert BATCH_LIMIT == 100
    names = captures(cfg, 105)
    observer = FakeObserver(cfg)
    loop = ObserveLoop(replace(cfg, mode="batch", parallel=4), observer)
    assert [await loop.step() for _ in range(3)] == ["observed", "observed", "idle"]
    assert observer.batches == [names[:100], names[100:]]


async def test_skipped_interactions_are_not_offered_again(cfg):
    a, b = captures(cfg, 2)
    add_skipped(cfg.state_dir / "skipped.txt", a, "boom")
    observer = FakeObserver(cfg)
    loop = ObserveLoop(cfg, observer)
    assert await loop.step() == "observed"
    assert await loop.step() == "idle"
    assert observer.batches == [[b]]


# -- rule 2: judge by the disk ------------------------------------------------------------------


async def test_some_written_is_observed(cfg):
    a, b = captures(cfg, 2)
    observer = FakeObserver(cfg, {a: "unreached"})
    loop = ObserveLoop(cfg, observer)
    assert await loop.step() == "observed"
    assert is_observed(cfg, b) and not is_observed(cfg, a)
    assert await loop.step() == "observed"
    assert observer.batches == [[a, b], [a]]


async def test_an_ok_outcome_without_files_is_a_failure_and_no_strike(cfg):
    (name,) = captures(cfg, 1)
    loop = ObserveLoop(cfg, FakeObserver(cfg, "lie", "lie", "lie"), max_attempts=1)
    assert [await loop.step() for _ in range(3)] == ["failed"] * 3
    assert skipped(cfg) == {}


async def test_half_written_is_redone(cfg):
    (name,) = captures(cfg, 1)
    observer = FakeObserver(cfg, "half", "ok")
    loop = ObserveLoop(cfg, observer)
    assert await loop.step() == "failed"
    assert evidence(cfg, "observations", name).read_text() == "call 1"
    assert not evidence(cfg, "operations", name).exists()

    assert await loop.step() == "observed"
    assert observer.batches == [[name], [name]]
    assert evidence(cfg, "observations", name).read_text() == "call 2"
    assert evidence(cfg, "operations", name).read_text() == "call 2"
    assert await loop.step() == "idle"


# -- rule 3: wait -------------------------------------------------------------------------------


async def test_wait_on_credits(cfg):
    names = captures(cfg, 3)
    sleeps = Sleeps(3)
    observer = FakeObserver(cfg, *[("wait", 21600)] * 3)
    await run_to_stop(ObserveLoop(cfg, observer, sleep=sleeps, max_attempts=1))
    assert sleeps.seconds == [21600] * 3
    assert skipped(cfg) == {}
    assert observer.batches == [names] * 3


async def test_wait_takes_the_largest(cfg):
    a, b, c = captures(cfg, 3)
    sleeps = Sleeps(1)
    observer = FakeObserver(cfg, {a: ("wait", 30), b: ("wait", 21600), c: "fail"})
    await run_to_stop(ObserveLoop(cfg, observer, sleep=sleeps))
    assert sleeps.seconds == [21600]


@pytest.mark.parametrize("wait", [0, -30])
async def test_a_wait_is_at_least_retry_initial(cfg, wait):
    captures(cfg, 2)
    sleeps = Sleeps(2)
    observer = FakeObserver(cfg, ("wait", wait), ("wait", wait))
    await run_to_stop(ObserveLoop(cfg, observer, sleep=sleeps))
    assert sleeps.seconds == [5, 5]


async def test_a_wait_leaves_the_backoff_alone(cfg):
    captures(cfg, 1)
    sleeps = Sleeps(4)
    observer = FakeObserver(cfg, "unreached", ("wait", 21600), "unreached", "unreached")
    await run_to_stop(ObserveLoop(cfg, observer, sleep=sleeps))
    assert sleeps.seconds == [5, 21600, 10, 20]


async def test_a_wait_with_some_written_is_observed_without_sleeping(cfg):
    a, b = captures(cfg, 2)
    sleeps = Sleeps(100)
    observer = FakeObserver(cfg, {a: ("wait", 21600)}, "ok")
    loop = ObserveLoop(cfg, observer, sleep=sleeps, rescan_seconds=0.01)
    await run_until(loop, settled(cfg))
    assert sleeps.seconds == []
    assert observer.batches == [[a, b], [a]]


# -- rule 4: back off ---------------------------------------------------------------------------


async def test_backoff_doubles_and_resets(cfg):
    captures(cfg, 2)
    sleeps = Sleeps(9)
    observer = FakeObserver(cfg, *["unreached"] * 8, "ok", "unreached")
    await run_to_stop(ObserveLoop(replace(cfg, parallel=1), observer, sleep=sleeps))
    assert sleeps.seconds == [5, 10, 20, 40, 80, 160, 300, 300, 5]


async def test_backoff_resets_after_an_idle_round(cfg):
    a, = captures(cfg, 1)
    sleeps = Sleeps(3, on_sleep=lambda n: write(cfg, a) if n == 1 else None)     # done by hand meanwhile
    observer = FakeObserver(cfg, "unreached", "unreached", "unreached")
    loop = ObserveLoop(cfg, observer, sleep=sleeps, rescan_seconds=0.01)
    task = asyncio.create_task(loop.run())
    await asyncio.sleep(0.1)        # the next round finds nothing: idle
    assert sleeps.seconds == [5]
    capture(cfg, "laptop/2026-10-25/11-00-00")
    with pytest.raises(StopRun):
        await asyncio.wait_for(task, 5)
    assert sleeps.seconds == [5, 5, 10]


async def test_wake_does_not_cut_a_backoff_short(cfg):
    captures(cfg, 1)
    observer = FakeObserver(cfg, "unreached", "unreached")

    async def slow_sleep(seconds: float) -> None:
        await asyncio.sleep(0.3)

    loop = ObserveLoop(cfg, observer, sleep=slow_sleep)
    task = asyncio.create_task(loop.run())
    try:
        await asyncio.wait_for(observer.called.wait(), 1)
        await asyncio.sleep(0.05)
        loop.wake()
        await asyncio.sleep(0.1)
        assert len(observer.batches) == 1       # still sleeping
        await asyncio.sleep(0.4)
        assert len(observer.batches) == 2
    finally:
        await cancel(task)


async def test_an_error_in_a_round_is_a_failed_round(cfg, caplog):
    captures(cfg, 1)
    (cfg.state_dir / "skipped.txt").mkdir(parents=True)     # unreadable as a file
    sleeps = Sleeps(3)
    observer = FakeObserver(cfg)
    loop = ObserveLoop(cfg, observer, sleep=sleeps)
    with caplog.at_level(logging.INFO, logger="vitality_observer"):
        await run_to_stop(loop)
    assert sleeps.seconds == [5, 10, 20]
    assert len([r for r in caplog.records if r.exc_info]) == 1      # the same error is logged once
    assert observer.batches == []


# -- rule 5: strike -----------------------------------------------------------------------------


async def test_strike_in_mixed_round(cfg):
    bad, *good = captures(cfg, 11)
    observer = FakeObserver(cfg, poison={bad})
    loop = ObserveLoop(cfg, observer)
    assert await loop.step() == "observed"
    assert observer.batches == [[bad, *good[:3]]]
    assert skipped(cfg) == {}               # one strike
    assert [await loop.step() for _ in range(2)] == ["observed", "observed"]
    assert observer.batches[1:] == [[bad, *good[3:6]], [bad, *good[6:9]]]
    assert skipped(cfg) == {bad: "boom"}    # the third strike
    assert await loop.step() == "observed"
    assert observer.batches[3] == [good[9]]


async def test_a_lone_failure_three_times_is_skipped(cfg):
    bad, *good = captures(cfg, 4)
    observer = FakeObserver(cfg, poison={bad})
    loop = ObserveLoop(replace(cfg, parallel=1), observer, sleep=Sleeps(100), rescan_seconds=0.01)
    await run_until(loop, settled(cfg))
    assert skipped(cfg) == {bad: "boom"}
    assert observer.batches == [[bad]] * 3 + [[name] for name in good]
    assert all(is_observed(cfg, name) for name in good)


async def test_two_bad_interactions_are_both_skipped(cfg):
    bad1, bad2, *good = captures(cfg, 8)
    observer = FakeObserver(cfg, poison={bad1, bad2})
    loop = ObserveLoop(cfg, observer, sleep=Sleeps(200), rescan_seconds=0.01)
    await run_until(loop, settled(cfg))
    assert skipped(cfg) == {bad1: "boom", bad2: "boom"}
    assert all(is_observed(cfg, name) for name in good)


async def test_a_failure_that_did_not_reach_the_model_is_never_struck(cfg):
    names = captures(cfg, 2)
    loop = ObserveLoop(cfg, FakeObserver(cfg, *["unreached"] * 6), max_attempts=1)
    for _ in range(6):
        assert await loop.step() == "failed"
    assert skipped(cfg) == {}


async def test_half_written_is_not_struck(cfg):
    captures(cfg, 1)
    loop = ObserveLoop(cfg, FakeObserver(cfg, "half", "half"), max_attempts=1)
    assert [await loop.step() for _ in range(2)] == ["failed", "failed"]
    assert skipped(cfg) == {}


async def test_a_failure_whose_files_are_on_disk_is_not_struck(cfg):
    (name,) = captures(cfg, 1)
    loop = ObserveLoop(cfg, FakeObserver(cfg, "fail+files"), max_attempts=1)
    assert await loop.step() == "observed"
    assert skipped(cfg) == {}


async def test_an_observed_interaction_loses_its_strikes(cfg):
    (name,) = captures(cfg, 1)
    observer = FakeObserver(cfg, "fail", "fail", "ok", "fail", "fail")
    loop = ObserveLoop(cfg, observer)
    for _ in range(3):
        await loop.step()
    evidence(cfg, "observations", name).unlink()       # to be observed again
    for _ in range(2):
        assert await loop.step() == "failed"
    assert skipped(cfg) == {}       # two strikes, not four


async def test_a_failed_skip_write_keeps_the_strikes(cfg, monkeypatch):
    (name,) = captures(cfg, 1)
    writes = []

    def failing_once(path, name, reason):
        writes.append(name)
        if len(writes) == 1:
            raise OSError("disk full")
        add_skipped(path, name, reason)

    monkeypatch.setattr(loop_module, "add_skipped", failing_once)
    loop = ObserveLoop(cfg, FakeObserver(cfg, *["fail"] * 5), max_attempts=2)
    assert await loop.step() == "failed"
    with pytest.raises(OSError):
        await loop.step()           # second strike: skipping it fails
    assert await loop.step() == "failed"        # a third strike, not a first: skipped now
    assert skipped(cfg) == {name: "boom"}


# -- wake, rescan, cancel -----------------------------------------------------------------------


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


# -- logging ------------------------------------------------------------------------------------


async def test_idle_resets_backoff_and_failure_logging(cfg, caplog):
    a, = captures(cfg, 1)
    sleeps = Sleeps(3, on_sleep=lambda n: write(cfg, a) if n == 1 else None)
    loop = ObserveLoop(cfg, FakeObserver(cfg, "fail", "fail", "fail"), sleep=sleeps, rescan_seconds=0.01)
    with caplog.at_level(logging.INFO, logger="vitality_observer"):
        task = asyncio.create_task(loop.run())
        await asyncio.sleep(0.1)
        capture(cfg, "laptop/2026-10-25/11-00-00")
        with pytest.raises(StopRun):
            await asyncio.wait_for(task, 5)
    assert sleeps.seconds == [5, 5, 10]
    assert len([r for r in caplog.records if "failed, retrying" in r.getMessage()]) == 2


async def test_failure_is_logged_again_when_its_detail_changes(cfg, caplog):
    captures(cfg, 2)
    observer = FakeObserver(cfg, "unreached", "unreached", "fail:y", "ok")
    loop = ObserveLoop(cfg, observer)
    with caplog.at_level(logging.INFO, logger="vitality_observer"):
        for _ in range(4):
            await loop.step()
    messages = [r.getMessage() for r in caplog.records]
    assert [m for m in messages if "failed, retrying" in m] == [
        "observing failed, retrying with backoff: no api", "observing failed, retrying with backoff: y"]
    assert len([m for m in messages if "works again" in m]) == 1


async def test_recovery_and_each_observed_round_are_logged(cfg, caplog):
    names = captures(cfg, 3)
    observer = FakeObserver(cfg, "unreached", {names[1]: "unreached", names[2]: "unreached"}, "ok")
    loop = ObserveLoop(cfg, observer)
    with caplog.at_level(logging.INFO, logger="vitality_observer"):
        for _ in range(3):
            await loop.step()
    messages = [r.getMessage() for r in caplog.records]
    assert len([m for m in messages if "works again" in m]) == 1
    assert messages.index("observing works again") + 1 == messages.index(f"observed 1 interaction(s), from {names[0]}")
    assert f"observed 2 interaction(s), from {names[1]}" in messages


async def test_a_skip_is_logged(cfg, caplog):
    (name,) = captures(cfg, 1)
    loop = ObserveLoop(cfg, FakeObserver(cfg, *["fail"] * 3))
    with caplog.at_level(logging.INFO, logger="vitality_observer"):
        for _ in range(3):
            await loop.step()
    skips = [r.getMessage() for r in caplog.records if "skipping" in r.getMessage()]
    assert len(skips) == 1 and name in skips[0] and "boom" in skips[0]


async def test_a_wait_is_logged_once_with_its_end(cfg, caplog):
    captures(cfg, 1)
    observer = FakeObserver(cfg, ("wait", 21600), ("wait", 21600), "ok")
    loop = ObserveLoop(cfg, observer, clock=lambda: 1000.0)
    with caplog.at_level(logging.INFO, logger="vitality_observer"):
        for _ in range(3):
            await loop.step()
    waits = [r.getMessage() for r in caplog.records if "waiting" in r.getMessage()]
    assert waits == ["out of API credits, waiting until 1970-01-01T06:16:40+00:00"]
    assert len([r for r in caplog.records if "works again" in r.getMessage()]) == 1
