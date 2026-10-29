import asyncio
import contextlib
import logging
from collections.abc import Callable, Collection, Sequence
from dataclasses import replace
from pathlib import Path

import pytest

from vitality_observer.claude import CallResult
from vitality_observer.config import ObserverConfig
from vitality_observer import loop as loop_module
from vitality_observer.loop import ObserveLoop
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


def observe(cfg: ObserverConfig, name: str, text: str = "seen") -> None:
    path = cfg.model_dir / "observations" / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def captures(cfg: ObserverConfig, count: int, device: str = "laptop") -> list[str]:
    names = [f"{device}/2026-10-25/10-00-{n:02d}" for n in range(count)]
    for name in names:
        capture(cfg, name)
    return names


def result(ok: bool, detail: str = "", limited: bool = False, reset: float | None = None,
           reached: bool = True) -> CallResult:
    return CallResult(ok=ok, usage_limit_reset=reset, usage_limited=limited, reached_model=reached,
                      detail=detail, record={})


class FakeObserver:
    """An Observer that plays back `outcomes`, then succeeds; remembers every call.

    An outcome is "ok" (writes every file), "fail" (reaches Claude, fails with detail "boom"),
    "fail:<detail>" (the same with that detail), "unreached" (fails without reaching Claude),
    "oserror" (raises), "limit", ("limit", reset_epoch), a number (succeeds but writes only
    that many files, oldest first), or ("fail", number) (fails with "boom" after writing that
    many files, oldest first).

    Once the outcomes run out, a call whose batch holds an interaction in `poison` fails like
    "fail", and the files of interactions in `unwritten` are never written.
    """

    def __init__(self, cfg: ObserverConfig, *outcomes: str | int | tuple[str, float],
                 on_call: Callable[[], None] | None = None,
                 poison: Collection[str] = (), unwritten: Collection[str] = ()):
        self.cfg = cfg
        self.on_call = on_call
        self.outcomes = list(outcomes)
        self.poison = poison
        self.unwritten = unwritten
        self.batches: list[list[str]] = []
        self.contexts: list[list[tuple[str, str]]] = []
        self.called = asyncio.Event()

    async def observe(self, batch: Sequence[Interaction], context: Sequence[tuple[str, str]]) -> CallResult:
        names = [i.name for i in batch]
        self.batches.append(names)
        self.contexts.append(list(context))
        self.called.set()
        if self.on_call:
            self.on_call()
        if self.outcomes:
            outcome = self.outcomes.pop(0)
        else:
            outcome = "fail" if any(name in self.poison for name in names) else "ok"
        failure = ""
        match outcome:
            case "fail":
                return result(False, "boom")
            case str(text) if text.startswith("fail:"):
                return result(False, text.removeprefix("fail:"))
            case "unreached":
                return result(False, "no claude", reached=False)
            case "oserror":
                raise OSError("disk trouble")
            case "limit":
                return result(False, "usage limit", limited=True)
            case ("limit", reset):
                return result(False, "usage limit", limited=True, reset=reset)
            case ("fail", int(count)):
                names, failure = names[:count], "boom"
            case int(count):
                names = names[:count]
        for name in names:
            if name not in self.unwritten:
                observe(self.cfg, name)
        return result(not failure, failure)


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
    return (cfg.model_dir / "observations" / f"{name}.md").is_file()


# -- batches ------------------------------------------------------------------------------------


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


async def test_batch_is_the_oldest_device_skipping_other_devices(cfg):
    for name in ("laptop/2026-10-25/10-00-00", "desktop/2026-10-25/10-00-01",
                 "laptop/2026-10-25/10-00-02", "laptop/2026-10-25/10-00-03"):
        capture(cfg, name)
    observer = FakeObserver(cfg)
    loop = ObserveLoop(cfg, observer)
    assert [await loop.step() for _ in range(3)] == ["observed", "observed", "idle"]
    assert observer.batches == [
        ["laptop/2026-10-25/10-00-00", "laptop/2026-10-25/10-00-02", "laptop/2026-10-25/10-00-03"],
        ["desktop/2026-10-25/10-00-01"],
    ]


async def test_context_passed_is_before_first_of_batch(cfg):
    observe(cfg, "laptop/2026-10-25/09-00-00", "earlier")
    observe(cfg, "desktop/2026-10-25/09-30-00", "other device")
    first, second = captures(cfg, 2)
    observer = FakeObserver(cfg)
    await ObserveLoop(cfg, observer).step()
    assert observer.batches == [[first, second]]
    assert observer.contexts == [[("laptop/2026-10-25/09-00-00", "earlier")]]


async def test_missing_files_are_taken_by_the_next_call(cfg):
    names = captures(cfg, 3)
    observer = FakeObserver(cfg, 2)
    loop = ObserveLoop(cfg, observer)
    assert await loop.step() == "observed"
    assert await loop.step() == "observed"
    assert observer.batches == [names, [names[2]]]
    assert await loop.step() == "idle"


# -- rule 1: back off ---------------------------------------------------------------------------


async def test_backoff_doubles_up_to_the_maximum(cfg):
    captures(cfg, 1)
    sleeps = Sleeps(9)
    await run_to_stop(ObserveLoop(cfg, FakeObserver(cfg, *["unreached"] * 9), sleep=sleeps))
    assert sleeps.seconds == [5, 10, 20, 40, 80, 160, 300, 300, 300]


async def test_a_call_that_writes_nothing_backs_off_whatever_ok_says(cfg):
    captures(cfg, 3)
    sleeps = Sleeps(8)
    await run_to_stop(ObserveLoop(cfg, FakeObserver(cfg, *[0] * 8), sleep=sleeps))
    assert sleeps.seconds == [5, 10, 20, 40, 80, 160, 300, 300]


async def test_backoff_resets_after_an_observed_round(cfg):
    captures(cfg, 2)
    sleeps = Sleeps(3)
    observer = FakeObserver(cfg, "unreached", "unreached", "ok", "unreached")
    await run_to_stop(ObserveLoop(replace(cfg, batch_size=1), observer, sleep=sleeps))
    assert sleeps.seconds == [5, 10, 5]


async def test_backoff_resets_after_an_idle_round(cfg):
    a, = captures(cfg, 1)
    sleeps = Sleeps(3, on_sleep=lambda n: observe(cfg, a) if n == 1 else None)     # done by hand meanwhile
    observer = FakeObserver(cfg, "unreached", "unreached", "unreached")
    loop = ObserveLoop(cfg, observer, sleep=sleeps, rescan_seconds=0.01)
    task = asyncio.create_task(loop.run())
    await asyncio.sleep(0.1)        # the next round finds nothing: idle
    assert sleeps.seconds == [5]
    capture(cfg, "laptop/2026-10-25/11-00-00")
    with pytest.raises(StopRun):
        await asyncio.wait_for(task, 5)
    assert sleeps.seconds == [5, 5, 10]


@pytest.mark.parametrize("reset,expected", [(900.0, 60), (1010.0, 60), (1120.0, 120), (None, 1800)])
async def test_usage_limit_wait(cfg, reset, expected):
    captures(cfg, 1)
    sleeps = Sleeps(1)
    outcome = "limit" if reset is None else ("limit", reset)
    loop = ObserveLoop(cfg, FakeObserver(cfg, outcome), sleep=sleeps, clock=lambda: 1000.0)
    await run_to_stop(loop)
    assert sleeps.seconds == [expected]


async def test_usage_limit_leaves_the_backoff_alone(cfg):
    captures(cfg, 1)
    sleeps = Sleeps(5)
    observer = FakeObserver(cfg, "unreached", "limit", "limit", "unreached", "unreached")
    await run_to_stop(ObserveLoop(cfg, observer, sleep=sleeps))
    assert sleeps.seconds == [5, 1800, 1800, 10, 20]


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


# -- rule 2: isolate ----------------------------------------------------------------------------


async def test_a_failed_batch_is_followed_by_the_oldest_alone_then_full_batches(cfg):
    names = captures(cfg, 13)
    observer = FakeObserver(cfg, "fail", "ok")
    loop = ObserveLoop(cfg, observer)
    assert await loop.step() == "failed"
    assert await loop.step() == "observed"
    assert await loop.step() == "observed"
    assert observer.batches == [names[:10], names[:1], names[1:11]]


async def test_isolation_lasts_until_a_round_is_observed(cfg):
    names = captures(cfg, 4)
    observer = FakeObserver(cfg, "unreached", "unreached", "unreached", "ok", "ok")
    loop = ObserveLoop(cfg, observer)
    for _ in range(5):
        await loop.step()
    assert observer.batches == [names, names[:1], names[:1], names[:1], names[1:]]


async def test_a_call_that_wrote_some_resumes_full_batches(cfg):
    names = captures(cfg, 5)
    observer = FakeObserver(cfg, "unreached", ("fail", 1), "ok")
    loop = ObserveLoop(cfg, observer)
    assert [await loop.step() for _ in range(3)] == ["failed", "observed", "observed"]
    assert observer.batches == [names, names[:1], names[1:]]


async def test_isolated_batches_stay_on_one_device(cfg):
    for name in ("laptop/2026-10-25/10-00-00", "desktop/2026-10-25/10-00-01",
                 "desktop/2026-10-25/10-00-02"):
        capture(cfg, name)
    observer = FakeObserver(cfg, "fail", "ok", "ok")
    loop = ObserveLoop(cfg, observer)
    for _ in range(3):
        await loop.step()
    assert observer.batches == [
        ["laptop/2026-10-25/10-00-00"], ["laptop/2026-10-25/10-00-00"],
        ["desktop/2026-10-25/10-00-01", "desktop/2026-10-25/10-00-02"],
    ]


async def test_a_usage_limit_does_not_isolate(cfg):
    names = captures(cfg, 3)
    observer = FakeObserver(cfg, "limit", "ok")
    loop = ObserveLoop(cfg, observer)
    assert await loop.step() == "failed"
    assert await loop.step() == "observed"
    assert observer.batches == [names, names]


# -- rule 3: strike -----------------------------------------------------------------------------


async def test_a_lone_interaction_that_fails_three_times_after_reaching_claude_is_skipped(cfg):
    bad, *good = captures(cfg, 4)
    observer = FakeObserver(cfg, poison={bad})
    loop = ObserveLoop(replace(cfg, batch_size=1), observer, sleep=Sleeps(100), rescan_seconds=0.01)
    await run_until(loop, settled(cfg))
    assert skipped(cfg) == {bad: "boom"}
    assert observer.batches == [[bad]] * 3 + [[name] for name in good]
    assert all(is_observed(cfg, name) for name in good)


async def test_a_lone_call_that_never_reaches_claude_is_never_struck(cfg):
    names = captures(cfg, 2)
    observer = FakeObserver(cfg, *["unreached"] * 12)
    loop = ObserveLoop(cfg, observer, max_attempts=1)
    for _ in range(12):
        assert await loop.step() == "failed"
    assert skipped(cfg) == {}
    assert observer.batches == [names] + [names[:1]] * 11


async def test_a_failed_batch_of_several_strikes_no_one(cfg):
    names = captures(cfg, 3)
    observer = FakeObserver(cfg, "fail", "fail")
    loop = ObserveLoop(replace(cfg, batch_size=2), observer, max_attempts=1)
    assert await loop.step() == "failed"        # [a, b]
    assert skipped(cfg) == {}
    assert await loop.step() == "failed"        # a alone: this one strikes, and max_attempts is 1
    assert skipped(cfg) == {names[0]: "boom"}
    assert observer.batches == [names[:2], names[:1]]


async def test_a_usage_limit_strikes_no_one(cfg):
    captures(cfg, 1)
    loop = ObserveLoop(cfg, FakeObserver(cfg, *["limit"] * 4), max_attempts=1)
    for _ in range(4):
        assert await loop.step() == "failed"
    assert skipped(cfg) == {}


async def test_an_error_in_a_round_strikes_no_one(cfg, caplog):
    captures(cfg, 1)
    sleeps = Sleeps(3)
    observer = FakeObserver(cfg, "oserror", "oserror", "oserror")
    loop = ObserveLoop(cfg, observer, sleep=sleeps, max_attempts=1)
    with caplog.at_level(logging.INFO, logger="vitality_observer"):
        await run_to_stop(loop)
    assert sleeps.seconds == [5, 10, 20]
    assert len([r for r in caplog.records if r.exc_info]) == 1      # the same error is logged once
    assert skipped(cfg) == {}
    assert len({tuple(batch) for batch in observer.batches}) == 1   # and the batch is not narrowed


async def test_a_lone_ok_call_that_writes_nothing_strikes(cfg):
    (name,) = captures(cfg, 1)
    observer = FakeObserver(cfg, 0, 0, 0)
    loop = ObserveLoop(cfg, observer)
    assert [await loop.step() for _ in range(3)] == ["failed"] * 3
    assert skipped(cfg) == {name: "no observation written"}
    assert await loop.step() == "idle"


async def test_files_missing_after_an_observed_call_strike_no_one(cfg):
    a, b = captures(cfg, 2)
    loop = ObserveLoop(cfg, FakeObserver(cfg, unwritten={a}), max_attempts=1)
    assert await loop.step() == "observed"      # b written, a not
    assert skipped(cfg) == {}


async def test_an_observed_interaction_loses_its_strikes(cfg):
    (name,) = captures(cfg, 1)
    observer = FakeObserver(cfg, "fail", "fail", "ok", "fail", "fail")
    loop = ObserveLoop(cfg, observer)
    for _ in range(3):
        await loop.step()
    (cfg.model_dir / "observations" / f"{name}.md").unlink()       # to be observed again
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


async def test_skipped_interactions_are_not_offered_again(cfg):
    a, b = captures(cfg, 2)
    add_skipped(cfg.state_dir / "skipped.txt", a, "boom")
    observer = FakeObserver(cfg)
    loop = ObserveLoop(cfg, observer)
    assert await loop.step() == "observed"
    assert await loop.step() == "idle"
    assert observer.batches == [[b]]


# -- regression scenarios -----------------------------------------------------------------------


async def test_bad_head_after_many_non_model_failures_is_skipped(cfg):
    """Scenario A: the backoff is at its maximum when a bad interaction reaches the head."""
    bad, *good = captures(cfg, 4)
    observer = FakeObserver(cfg, *["unreached"] * 8, poison={bad})
    sleeps = Sleeps(100)
    loop = ObserveLoop(cfg, observer, sleep=sleeps, rescan_seconds=0.01)
    await run_until(loop, settled(cfg))
    assert 300 in sleeps.seconds
    assert skipped(cfg) == {bad: "boom"}
    assert observer.batches[8:] == [[bad]] * 3 + [good[:1], good[1:]]
    assert all(is_observed(cfg, name) for name in good)


async def test_two_bad_interactions_in_a_row_are_both_skipped(cfg):
    """Scenario B: the second bad interaction must not stall the loop behind the first."""
    bad1, bad2, *good = captures(cfg, 8)
    observer = FakeObserver(cfg, poison={bad1, bad2})
    loop = ObserveLoop(cfg, observer, sleep=Sleeps(200), rescan_seconds=0.01)
    await run_until(loop, settled(cfg))
    assert skipped(cfg) == {bad1: "boom", bad2: "boom"}
    assert all(is_observed(cfg, name) for name in good)


async def test_partial_timeouts_skip_no_one(cfg):
    """Scenario C: every call writes its 3 oldest, then times out after reaching Claude."""
    names = captures(cfg, 30)
    sleeps = Sleeps(100)
    observer = FakeObserver(cfg, *[("fail", 3)] * 40)
    loop = ObserveLoop(cfg, observer, sleep=sleeps, rescan_seconds=0.01)
    await run_until(loop, settled(cfg))
    assert skipped(cfg) == {}
    assert all(is_observed(cfg, name) for name in names)
    assert sleeps.seconds == []         # every round was observed: no backoff


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


async def test_usage_limit_after_some_files_is_observed_and_the_next_call_waits(cfg):
    a, b = captures(cfg, 2)
    calls = []

    class LimitAfterOne:
        async def observe(self, batch, context):
            calls.append([i.name for i in batch])
            if len(calls) == 1:
                observe(cfg, batch[0].name)
            return result(False, "usage limit", limited=True, reset=1120.0)

    sleeps = Sleeps(1)
    loop = ObserveLoop(cfg, LimitAfterOne(), sleep=sleeps, clock=lambda: 1000.0, max_attempts=1)
    await run_to_stop(loop)
    assert calls == [[a, b], [b]]       # observed first, no sleep; the second call waits
    assert sleeps.seconds == [120]
    assert skipped(cfg) == {}


# -- logging ------------------------------------------------------------------------------------


async def test_idle_resets_backoff_and_failure_logging(cfg, caplog):
    a, = captures(cfg, 1)
    sleeps = Sleeps(3, on_sleep=lambda n: observe(cfg, a) if n == 1 else None)
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
    observer = FakeObserver(cfg, "fail:x", "fail:x", "fail:y", "ok")
    loop = ObserveLoop(cfg, observer)
    with caplog.at_level(logging.INFO, logger="vitality_observer"):
        for _ in range(4):
            await loop.step()
    messages = [r.getMessage() for r in caplog.records]
    assert [m for m in messages if "failed, retrying" in m] == [
        "observing failed, retrying with backoff: x", "observing failed, retrying with backoff: y"]
    assert len([m for m in messages if "works again" in m]) == 1


async def test_recovery_and_each_observed_batch_are_logged(cfg, caplog):
    names = captures(cfg, 3)
    observer = FakeObserver(cfg, "fail", 0, "ok", "ok")
    loop = ObserveLoop(cfg, observer, max_attempts=100)
    with caplog.at_level(logging.INFO, logger="vitality_observer"):
        for _ in range(4):
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


async def test_a_usage_limit_wait_is_logged_once_with_its_end(cfg, caplog):
    captures(cfg, 1)
    observer = FakeObserver(cfg, ("limit", 1120.0), ("limit", 1120.0), "ok")
    loop = ObserveLoop(cfg, observer, clock=lambda: 1000.0)
    with caplog.at_level(logging.INFO, logger="vitality_observer"):
        for _ in range(3):
            await loop.step()
    waits = [r.getMessage() for r in caplog.records if "usage limit" in r.getMessage()]
    assert waits == ["usage limit reached, waiting until 1970-01-01T00:18:40+00:00"]
    assert len([r for r in caplog.records if "works again" in r.getMessage()]) == 1
