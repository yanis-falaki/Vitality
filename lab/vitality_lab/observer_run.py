"""The observer stage of a lab run: its settings, and observing a dataset into a run folder.

The observing is the observer's own (`AnthropicObserver` in production): the same request,
parsing, files and `calls.jsonl`. Only the driving differs from the service: every interaction
is tried once, in order, `parallel` at a time; failures are recorded and the run goes on.
"""

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace

from vitality_observer.config import ObserverConfig, load_observer_config
from vitality_observer.provider.interface import Observer
from vitality_observer.work import WorkItem, interactions

SETTINGS = ("model", "effort", "parallel", "call_timeout_seconds")
"""The observer settings a run takes from the environment and lets `--set` override."""

MAX_WAIT_SECONDS = 300
"""A wait the account asks for (rate limit, overload) up to this long is waited; a longer one stops the run."""

RETRY_SECONDS = 30
"""The wait before trying again after a failure that is not the interaction's own (overload, a server
or connection error, a timeout) and for which the API named no wait."""

MAX_WAITS = 5
"""Waits one interaction may cause before it counts as failed."""


@dataclass
class RunResult:
    ok: int = 0
    failures: dict[str, str] = field(default_factory=dict)  # name → why it failed
    not_run: list[str] = field(default_factory=list)         # names never observed because the run stopped
    stopped: str | None = None                               # why the run stopped early, or None
    cost_usd: float = 0.0


def observer_settings(environ: Mapping[str, str], overrides: Sequence[str]) -> ObserverConfig:
    """The observer settings for a run: `OBSERVER_<SETTING>` from `environ`, then `<setting>=<value>` overrides.

    Validated as the observer validates them; the mode is always direct. Raises ValueError for an
    override without "=", a name not in `SETTINGS`, or a value the observer rejects.
    """
    env = {f"OBSERVER_{name.upper()}": environ[f"OBSERVER_{name.upper()}"]
           for name in SETTINGS if f"OBSERVER_{name.upper()}" in environ}
    for override in overrides:
        name, equals, value = override.partition("=")
        if not equals:
            raise ValueError(f"a setting is <name>=<value>, got {override!r}")
        if name not in SETTINGS:
            raise ValueError(f"unknown setting {name!r}; one of {', '.join(SETTINGS)}")
        env[f"OBSERVER_{name.upper()}"] = value
    return replace(load_observer_config(env), mode="direct")


def settings_record(config: ObserverConfig) -> dict:
    """The settings of `config` that a run records."""
    return {name: getattr(config, name) for name in SETTINGS}


async def run_observer(config: ObserverConfig, observer: Observer, *,
                       sleep: Callable[[float], Awaitable[object]] = asyncio.sleep,
                       echo: Callable[[str], None] = print) -> RunResult:
    """Observe every interaction under `config.captures_dir` into `config.model_dir`, oldest first.

    `config.parallel` at a time. A failure of the interaction's own (`reached_model`) is recorded and
    the run goes on. Otherwise the account is the problem: an outcome asking to wait at most
    `MAX_WAIT_SECONDS`, or naming no wait (then `RETRY_SECONDS`), is waited and that interaction
    tried again (after `MAX_WAITS` waits it fails); a longer wait stops the run, as does
    cancellation (Ctrl-C): whatever was not
    observed is in `not_run`, and the result is returned either way. One line per outcome goes
    to `echo`.
    """
    result = RunResult()
    queue: list[WorkItem] = interactions(config)
    waits: dict[str, int] = {}
    while queue:
        chunk, queue = queue[:config.parallel], queue[config.parallel:]
        try:
            outcomes = await observer.observe(chunk)
        except asyncio.CancelledError:
            return _stop(result, "interrupted", chunk + queue)
        retry: list[WorkItem] = []
        longest = 0.0
        stopping: list[WorkItem] = []
        stop_reason = None
        for item, outcome in zip(chunk, outcomes):
            result.cost_usd += outcome.record.get("cost") or 0
            wait = outcome.wait_seconds
            if wait is None and not outcome.reached_model:
                wait = RETRY_SECONDS
            if outcome.ok:
                result.ok += 1
                echo(f"ok   {item.name}")
            elif wait is not None and wait <= MAX_WAIT_SECONDS:
                waits[item.name] = waits.get(item.name, 0) + 1
                if waits[item.name] > MAX_WAITS:
                    _fail(result, item, f"kept being asked to wait: {outcome.detail}", echo)
                else:
                    echo(f"wait {item.name}: {outcome.detail} ({wait:g} s)")
                    retry.append(item)
                    longest = max(longest, wait)
            elif wait is not None:
                stopping.append(item)
                stop_reason = outcome.detail
            else:
                _fail(result, item, outcome.detail, echo)
        if stop_reason is not None:
            return _stop(result, stop_reason, stopping + retry + queue)
        if retry:
            try:
                await sleep(longest)
            except asyncio.CancelledError:
                return _stop(result, "interrupted", retry + queue)
            queue = retry + queue
    return result


def _fail(result: RunResult, item: WorkItem, detail: str, echo: Callable[[str], None]) -> None:
    result.failures[item.name] = detail
    echo(f"fail {item.name}: {detail}")


def _stop(result: RunResult, reason: str, left: Sequence[WorkItem]) -> RunResult:
    result.stopped = reason
    result.not_run = [item.name for item in left]
    return result
