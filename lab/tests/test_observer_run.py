import asyncio
from collections.abc import Sequence
from pathlib import Path

import pytest
from vitality_observer.config import ObserverConfig
from vitality_observer.provider.interface import Outcome
from vitality_observer.work import WorkItem

from vitality_lab.observer_run import observer_settings, run_observer

Step = tuple[bool, str, float | None] | tuple[bool, str, float | None, bool] | type[asyncio.CancelledError]
# (ok, detail, wait_seconds[, reached_model]), or raise; reached_model defaults to True


class FakeObserver:
    """Answers each interaction from its script (ok by default, cost 0.02), in order; records batch sizes."""

    def __init__(self, script: dict[str, list[Step]] | None = None):
        self.script = script or {}
        self.batches: list[int] = []

    async def observe(self, batch: Sequence[WorkItem]) -> list[Outcome]:
        self.batches.append(len(batch))
        outcomes = []
        for item in batch:
            steps = self.script.get(item.name, [])
            step = steps.pop(0) if len(steps) > 1 else (steps[0] if steps else (True, "", None))
            if step is asyncio.CancelledError:
                raise asyncio.CancelledError
            assert isinstance(step, tuple)
            ok, detail, wait = step[:3]
            reached = step[3] if len(step) > 3 else True
            outcomes.append(Outcome(item.name, ok, reached, detail, wait, {"cost": 0.02 if ok else None}))
        return outcomes


def dataset(tmp_path: Path, count: int) -> tuple[ObserverConfig, list[str]]:
    names = [f"laptop/2026-11-0{i}/10-00-00" for i in range(1, count + 1)]
    for name in names:
        folder = tmp_path / "captures" / name
        folder.mkdir(parents=True)
        (folder / "interaction.json").write_text("{}")
    run = tmp_path / "run"
    return ObserverConfig(captures_dir=tmp_path / "captures", model_dir=run, state_dir=run), names


async def run(config: ObserverConfig, observer: FakeObserver, parallel: int, sleeps: list[float] | None = None):
    async def sleep(seconds: float) -> None:
        if sleeps is not None:
            sleeps.append(seconds)
    from dataclasses import replace
    return await run_observer(replace(config, parallel=parallel), observer, sleep=sleep, echo=lambda line: None)


async def test_all_ok(tmp_path: Path):
    config, _ = dataset(tmp_path, 5)
    observer = FakeObserver()
    result = await run(config, observer, 2)
    assert observer.batches == [2, 2, 1]
    assert result.ok == 5
    assert result.cost_usd == pytest.approx(0.10)
    assert result.stopped is None and result.failures == {} and result.not_run == []


async def test_failure_recorded_and_run_goes_on(tmp_path: Path):
    config, names = dataset(tmp_path, 5)
    result = await run(config, FakeObserver({names[1]: [(False, "bad reply", None)]}), 2)
    assert result.failures == {names[1]: "bad reply"}
    assert result.ok == 4


async def test_short_wait_retried(tmp_path: Path):
    config, names = dataset(tmp_path, 5)
    sleeps: list[float] = []
    result = await run(config, FakeObserver({names[2]: [(False, "rate limited", 30), (True, "", None)]}), 2, sleeps)
    assert sleeps == [30]
    assert result.ok == 5


async def test_account_failure_without_wait_retried(tmp_path: Path):
    config, names = dataset(tmp_path, 3)
    sleeps: list[float] = []
    observer = FakeObserver({names[1]: [(False, "api 529: Overloaded", None, False), (True, "", None)]})
    result = await run(config, observer, 1, sleeps)
    assert sleeps == [30]
    assert result.ok == 3 and result.failures == {}


async def test_endless_wait_becomes_failure(tmp_path: Path):
    config, names = dataset(tmp_path, 3)
    sleeps: list[float] = []
    result = await run(config, FakeObserver({names[0]: [(False, "rate limited", 30)]}), 1, sleeps)
    assert sleeps == [30] * 5
    assert result.failures[names[0]].startswith("kept being asked to wait")
    assert result.ok == 2


async def test_long_wait_stops(tmp_path: Path):
    config, names = dataset(tmp_path, 5)
    result = await run(config, FakeObserver({names[2]: [(False, "out of API credits", 21600)]}), 1)
    assert result.ok == 2
    assert result.not_run == names[2:]
    assert result.stopped == "out of API credits"


async def test_cancel_stops(tmp_path: Path):
    config, names = dataset(tmp_path, 5)
    result = await run(config, FakeObserver({names[1]: [asyncio.CancelledError]}), 1)
    assert result.ok == 1
    assert result.not_run == names[1:]
    assert result.stopped == "interrupted"


def test_settings_from_env_and_overrides():
    config = observer_settings({"OBSERVER_MODEL": "m1", "OBSERVER_MODE": "batch"}, ["effort=medium", "parallel=2"])
    assert (config.model, config.effort, config.parallel, config.mode) == ("m1", "medium", 2, "direct")


@pytest.mark.parametrize(("override", "message"), [
    ("mode=batch", "unknown setting 'mode'"),
    ("parallel=0", "OBSERVER_PARALLEL"),
    ("effort", "="),
])
def test_bad_overrides(override: str, message: str):
    with pytest.raises(ValueError, match=message):
        observer_settings({}, [override])
