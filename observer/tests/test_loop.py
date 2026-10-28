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
    """A sleep that records its argument, lets other tasks run, and ends `run()` after `limit` calls."""

    def __init__(self, limit: int):
        self.limit = limit
        self.seconds: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.seconds.append(seconds)
        if len(self.seconds) >= self.limit:
            raise StopRun
        await asyncio.sleep(0)


async def run_until(loop: ObserveLoop, done: Callable[[], bool], seconds: float = 5) -> None:
    """Run `loop.run()` until `done()`; fail, rather than hang, if it takes over `seconds` or `run()` stops."""
    task = asyncio.create_task(loop.run())
    try:
        async with asyncio.timeout(seconds):
            while not done():
                if task.done():
                    pytest.fail(f"run() stopped before the end: {task.exception()!r}")
                await asyncio.sleep(0.001)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, StopRun):
            await task


def settled(cfg: ObserverConfig) -> Callable[[], bool]:
    """Whether every interaction is observed or skipped."""
    return lambda: not unobserved(cfg, read_skipped(cfg.state_dir / "skipped.txt"))


def skipped(cfg: ObserverConfig) -> dict[str, str]:
    return read_skipped(cfg.state_dir / "skipped.txt")


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
    a, b = captures(cfg, 2)
    observer = FakeObserver(cfg, unwritten={a})
    loop = ObserveLoop(cfg, observer)
    for newer in ("laptop/2026-10-25/11-00-00", "laptop/2026-10-25/12-00-00", None):
        assert await loop.step() == "observed"      # writes the others, never `a`
        if newer:
            capture(cfg, newer)
    assert len(observer.batches) == 3 and all(batch[0] == a for batch in observer.batches)
    assert skipped(cfg) == {a: "no observation written"}


async def test_a_bad_interaction_is_skipped_after_three_proven_failures(cfg):
    bad, *good = captures(cfg, 4)
    observer = FakeObserver(cfg, poison={bad})
    loop = ObserveLoop(replace(cfg, batch_size=1), observer)
    while await loop.step() != "idle":
        assert len(observer.batches) < 20
    # `bad` fails alone and becomes the suspect; each probe that succeeds counts one attempt.
    assert observer.batches == [[bad], [good[0]], [bad], [good[1]], [bad], [good[2]]]
    assert skipped(cfg) == {bad: "boom"}


async def test_failed_batch_is_followed_by_a_single_and_counts_nothing(cfg):
    names = captures(cfg, 3)
    observer = FakeObserver(cfg, "fail", "ok", "ok")
    loop = ObserveLoop(cfg, observer, max_attempts=1)
    assert await loop.step() == "failed"
    assert skipped(cfg) == {}    # even with one attempt allowed
    assert await loop.step() == "observed"
    assert await loop.step() == "observed"
    assert observer.batches == [names, [names[0]], names[1:]]   # full batches resume after success


async def test_call_that_writes_nothing_is_a_failure_and_counts_nothing_for_a_batch(cfg):
    names = captures(cfg, 3)
    observer = FakeObserver(cfg, 0, "ok")
    loop = ObserveLoop(cfg, observer, max_attempts=1)
    assert await loop.step() == "failed"
    assert skipped(cfg) == {}
    assert await loop.step() == "observed"
    assert observer.batches == [names, [names[0]]]


async def test_failed_call_that_wrote_some_takes_the_observed_path(cfg):
    a, b, c = captures(cfg, 3)
    observer = FakeObserver(cfg, ("fail", 2), "ok")
    loop = ObserveLoop(cfg, observer, max_attempts=1)
    assert await loop.step() == "observed"      # Claude worked: `a` and `b` are done
    assert skipped(cfg) == {c: "boom"}          # and the missing one counts an attempt
    assert await loop.step() == "idle"


async def test_persistent_write_nothing_backs_off_and_skips_nothing(cfg):
    captures(cfg, 3)
    sleeps = Sleeps(12)
    loop = ObserveLoop(cfg, FakeObserver(cfg, *[0] * 12), sleep=sleeps)
    with pytest.raises(StopRun):
        await loop.run()
    assert sleeps.seconds == [5, 10, 20, 40, 80, 160] + [300] * 6       # no hot loop
    # The batch of 3 counts nothing; the oldest then fails alone and becomes the suspect, but
    # no call ever succeeds to prove Claude works, so nobody is blamed.
    assert skipped(cfg) == {}


async def test_a_lone_failure_makes_a_suspect_and_the_next_call_probes(cfg):
    bad, good1, good2 = captures(cfg, 3)
    observer = FakeObserver(cfg, "fail", "fail", "ok", "ok")
    loop = ObserveLoop(cfg, observer, max_attempts=1)
    assert await loop.step() == "failed"        # the batch: no one blamed, the next call is alone
    assert await loop.step() == "failed"        # `bad` alone: now the suspect, still not blamed
    assert skipped(cfg) == {}
    assert await loop.step() == "observed"      # the probe works, so `bad` is blamed
    assert skipped(cfg) == {bad: "boom"}
    assert await loop.step() == "observed"      # and full batches resume
    assert observer.batches == [[bad, good1, good2], [bad], [good1], [good2]]


async def test_a_failing_probe_makes_no_second_suspect(cfg):
    a, b, c = captures(cfg, 3)
    observer = FakeObserver(cfg, "fail", "fail", "fail", "ok")
    loop = ObserveLoop(cfg, observer, max_attempts=1)
    for _ in range(3):
        assert await loop.step() == "failed"
    assert await loop.step() == "observed"
    # `a` is the suspect; `b` failed as a probe, so the next probe is `c`; only `a` is blamed.
    assert observer.batches == [[a, b, c], [a], [b], [c]]
    assert skipped(cfg) == {a: "boom"}


async def test_only_the_suspect_waiting_is_idle_until_new_work_arrives(cfg):
    (suspect,) = captures(cfg, 1)
    observer = FakeObserver(cfg, "fail")
    loop = ObserveLoop(cfg, observer, max_attempts=1)
    assert await loop.step() == "failed"
    assert skipped(cfg) == {}                   # not blamed before Claude is shown to work
    assert await loop.step() == "idle"
    assert observer.batches == [[suspect]]      # no call for the suspect alone
    newer = "laptop/2026-10-25/11-00-00"
    capture(cfg, newer)
    assert await loop.step() == "observed"
    assert observer.batches[-1] == [newer]      # the new interaction is the probe
    assert skipped(cfg) == {suspect: "boom"}    # the idle round kept the suspect


async def test_bad_head_after_many_unreached_failures_is_skipped(cfg):
    """Scenario A: the backoff is at its maximum when a bad interaction reaches the head."""
    bad, *good = captures(cfg, 4)
    observer = FakeObserver(cfg, *["unreached"] * 8, poison={bad})
    sleeps = Sleeps(100)
    loop = ObserveLoop(cfg, observer, sleep=sleeps, rescan_seconds=0.01)
    await asyncio.wait_for(run_until(loop, settled(cfg)), 10)
    assert 300 in sleeps.seconds
    assert skipped(cfg) == {bad: "boom"}
    # After the outage: three cycles of a failed batch, `bad` alone (the suspect), a good probe.
    assert observer.batches[8:] == [[bad], good[:1], [bad, *good[1:]], [bad], good[1:2],
                                    [bad, *good[2:]], [bad], good[2:]]


async def test_two_bad_interactions_in_a_row_are_both_skipped(cfg):
    """Scenario B: the second bad interaction must not stall the loop behind the first."""
    bad1, bad2, *good = captures(cfg, 8)
    observer = FakeObserver(cfg, poison={bad1, bad2})
    loop = ObserveLoop(cfg, observer, sleep=Sleeps(200), rescan_seconds=0.01)
    await asyncio.wait_for(run_until(loop, settled(cfg)), 10)
    assert skipped(cfg) == {bad1: "boom", bad2: "boom"}
    assert not any(name in skipped(cfg) for name in good)


@pytest.mark.parametrize("max_attempts,expected", [(1, "head skipped"), (2, "nothing skipped")])
async def test_a_long_outage_costs_only_the_head_one_attempt(cfg, max_attempts, expected):
    head, *rest = captures(cfg, 3)
    observer = FakeObserver(cfg, *["fail"] * 20)       # every call reaches Claude and fails
    sleeps = Sleeps(100)
    loop = ObserveLoop(cfg, observer, sleep=sleeps, rescan_seconds=0.01, max_attempts=max_attempts)
    await asyncio.wait_for(run_until(loop, settled(cfg)), 10)
    assert sleeps.seconds[-1] == 300        # the backoff still grew
    # One attempt in all: with one allowed the head alone is skipped, with two no one is.
    assert skipped(cfg) == ({head: "boom"} if max_attempts == 1 else {})


async def test_failures_that_never_reach_claude_are_never_skipped(cfg):
    names = captures(cfg, 2)
    sleeps = Sleeps(10)
    observer = FakeObserver(cfg, *["unreached"] * 10)
    loop = ObserveLoop(cfg, observer, sleep=sleeps, max_attempts=2)
    with pytest.raises(StopRun):
        await loop.run()
    assert skipped(cfg) == {}
    assert observer.batches[1:] == [[names[0]]] * 9      # alone after the first, never a suspect
    assert sleeps.seconds == [5, 10, 20, 40, 80, 160, 300, 300, 300, 300]   # the backoff still grows


async def test_unreached_call_that_wrote_nothing_makes_no_suspect(cfg):
    first, second = captures(cfg, 2)

    class WritesNothing:
        def __init__(self):
            self.batches: list[list[str]] = []

        async def observe(self, batch, context):
            self.batches.append([i.name for i in batch])
            return CallResult(ok=True, usage_limit_reset=None, usage_limited=False, reached_model=False,
                              detail="", record={})

    observer = WritesNothing()
    loop = ObserveLoop(cfg, observer, max_attempts=1)
    assert await loop.step() == "failed"
    assert await loop.step() == "failed"
    assert await loop.step() == "failed"
    assert observer.batches == [[first, second], [first], [first]]     # never a probe
    assert skipped(cfg) == {}


async def test_failure_backoff_doubles(cfg):
    captures(cfg, 1)
    sleeps = Sleeps(8)
    loop = ObserveLoop(cfg, FakeObserver(cfg, *["unreached"] * 8), sleep=sleeps)
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
    names = captures(cfg, 2)
    sleeps = Sleeps(4)
    observer = FakeObserver(cfg, "limit", "limit", "limit", "fail", "ok")
    loop = ObserveLoop(cfg, observer, sleep=sleeps, clock=lambda: 1000.0, max_attempts=1)
    with pytest.raises(StopRun):
        await loop.run()
    # three limits (no attempts, backoff untouched, still full batches), then one real failure
    # sleeping the initial backoff
    assert sleeps.seconds == [1800, 1800, 1800, 5]
    assert skipped(cfg) == {}
    assert await loop.step() == "observed"
    assert observer.batches == [names] * 4 + [names[:1]]


async def test_usage_limited_lone_interaction_is_not_a_suspect(cfg):
    (name,) = captures(cfg, 1)
    observer = FakeObserver(cfg, "limit")
    loop = ObserveLoop(cfg, observer)
    assert await loop.step() == "failed"
    assert await loop.step() == "observed"      # offered again, not held back as a suspect
    assert observer.batches == [[name], [name]]


async def test_usage_limit_wait_has_a_floor(cfg):
    captures(cfg, 1)
    sleeps = Sleeps(2)
    observer = FakeObserver(cfg, ("limit", 900.0), ("limit", 1010.0))     # reset passed; reset in 10 s
    loop = ObserveLoop(cfg, observer, sleep=sleeps, clock=lambda: 1000.0)
    with pytest.raises(StopRun):
        await loop.run()
    assert sleeps.seconds == [60, 60]


async def test_error_in_a_round_is_retried_and_logged_once(cfg, caplog):
    (name,) = captures(cfg, 1)
    sleeps = Sleeps(3)
    loop = ObserveLoop(cfg, FakeObserver(cfg, "oserror", "oserror", "oserror"), sleep=sleeps, max_attempts=1)
    with caplog.at_level(logging.INFO, logger="vitality_observer"), pytest.raises(StopRun):
        await loop.run()
    assert sleeps.seconds == [5, 10, 20]
    assert len([r for r in caplog.records if r.exc_info]) == 1
    assert skipped(cfg) == {}
    assert await loop.step() == "observed"      # no suspect: offered as usual


async def test_idle_resets_backoff_and_logging(cfg, caplog):
    capture(cfg, "laptop/2026-10-25/10-00-00")
    sleeps = Sleeps(3)
    observer = FakeObserver(cfg, "fail", "fail", "fail")
    loop = ObserveLoop(cfg, observer, sleep=sleeps, rescan_seconds=0.02)
    with caplog.at_level(logging.INFO, logger="vitality_observer"):
        task = asyncio.create_task(loop.run())
        await asyncio.sleep(0.2)        # failed alone: the suspect, the only one waiting, so idle
        assert sleeps.seconds == [5]
        capture(cfg, "laptop/2026-10-25/10-00-01")
        with pytest.raises(StopRun):
            await asyncio.wait_for(task, 2)
    assert sleeps.seconds == [5, 5, 10]     # the probe's failure starts the backoff afresh
    assert len([r for r in caplog.records if "failed, retrying" in r.getMessage()]) == 2


async def test_failure_is_logged_again_when_its_detail_changes(cfg, caplog):
    a, b = captures(cfg, 2)
    observer = FakeObserver(cfg, "fail:x", "fail:x", "fail:y", "ok")
    loop = ObserveLoop(cfg, observer)
    with caplog.at_level(logging.INFO, logger="vitality_observer"):
        for _ in range(4):
            await loop.step()
    messages = [r.getMessage() for r in caplog.records]
    assert [m for m in messages if "failed, retrying" in m] == [
        "observing failed, retrying with backoff: x", "observing failed, retrying with backoff: y"]
    assert any("suspect" in m and a in m for m in messages)
    assert any("counting a failed attempt against " + a in m for m in messages)


async def test_success_logs_recovery_only_when_something_was_observed(cfg, caplog):
    captures(cfg, 2)
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
    observer = FakeObserver(cfg, 1, "ok", 1)
    loop = ObserveLoop(cfg, observer, max_attempts=2)
    await loop.step()       # first written, `b` missing: one attempt
    await loop.step()       # `b` written
    (cfg.model_dir / "observations" / f"{b}.md").unlink()
    earlier = "laptop/2026-10-25/09-00-00"
    capture(cfg, earlier)
    assert await loop.step() == "observed"      # `earlier` written, `b` missing: its first attempt again
    assert observer.batches[-1] == [earlier, b]
    assert skipped(cfg) == {}


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


async def test_a_failed_skip_write_keeps_the_count(cfg, monkeypatch):
    a, _ = captures(cfg, 2)
    writes = []

    def failing_once(path, name, reason):
        writes.append(name)
        if len(writes) == 1:
            raise OSError("disk full")
        add_skipped(path, name, reason)

    monkeypatch.setattr(loop_module, "add_skipped", failing_once)
    loop = ObserveLoop(cfg, FakeObserver(cfg, unwritten={a}), max_attempts=2)
    assert await loop.step() == "observed"      # `a` missing: attempt 1
    capture(cfg, "laptop/2026-10-25/11-00-00")
    with pytest.raises(OSError):
        await loop.step()                       # attempt 2: skipping it fails
    capture(cfg, "laptop/2026-10-25/12-00-00")
    assert await loop.step() == "observed"      # attempt 3, not 1: skipped now
    assert skipped(cfg) == {a: "no observation written"}


async def test_a_suspect_observed_meanwhile_is_forgotten_unblamed(cfg):
    a, b, _ = captures(cfg, 3)
    observer = FakeObserver(cfg, "fail", "fail")
    loop = ObserveLoop(cfg, observer, max_attempts=1)
    await loop.step()
    await loop.step()       # `a` alone: the suspect
    observe(cfg, a)         # written by hand meanwhile
    assert await loop.step() == "observed"
    assert skipped(cfg) == {}
