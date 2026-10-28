"""The observer's main loop: find unobserved interactions, hand them to Claude in batches, account for the outcome."""

# == Contract ==

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from typing import Literal, Protocol

from vitality_observer.claude import Observer
from vitality_observer.config import ObserverConfig
from vitality_observer.work import Interaction, add_skipped, context, device_of, read_skipped, unobserved

Step = Literal["idle", "observed", "failed"]
"""What one round did.
"idle": nothing was waiting, so no call was made.
"observed": a call wrote at least one observation of its batch, whatever `ok` says (a usage
limit that hit after some files were written counts too).
"failed": a call wrote none, even if `ok`, or hit a usage limit before writing any."""


class Loop(Protocol):
    """Keeps every interaction observed, one Claude call at a time, by three rules.

    A batch is the oldest waiting interaction plus the ones right after it from the same
    device, at most `batch_size`. Parameters named below belong to `ObserveLoop`.

    1. Back off. After a failed call, sleep `retry_initial` seconds (5), doubling each time up
       to `retry_max` (300); an observed or idle round resets it. A usage limit instead waits
       until its reported reset, at least `usage_wait_min` (60), or `usage_wait` (1800) if none
       was reported, and leaves the backoff as it was. `wake()` does not cut a sleep short.
    2. Isolate. After a failed call that was not a usage limit, the next batch is the oldest
       interaction alone; after an observed round, full batches resume. A usage limit changes
       neither.
    3. Strike. An interaction gets a strike only when a call for it alone failed and reached
       the model (`reached_model`; a timeout counts). Strikes are kept in memory and cleared
       when a call writes the interaction's observation. At `max_attempts` (3) strikes it is
       added to `skipped.txt` and never offered again; deleting its line offers it again.
       Nothing else strikes: a failed batch of several, a failure that did not reach the
       model, a usage limit, an error raised in a round, or files missing after an observed call.
    """

    def wake(self) -> None:
        """Tell the loop a new interaction arrived, so an idle `run()` looks at once."""
        ...

    async def run(self) -> None:
        """Repeat `step()` until cancelled.

        After "observed" it goes straight on; after "idle" it waits for `wake()` or
        `rescan_seconds`; after "failed" it sleeps as rule 1 says. An OSError or ValueError
        from a round is a failed round for rule 1 only: no isolation, no strike. It logs the
        start of a failure streak and each change of its detail, the start of a usage-limit
        wait (with its end), recovery once, each skip, and each observed batch.
        """
        ...

    async def step(self) -> Step:
        """One round: pick a batch, make one Claude call, apply rules 2 and 3 to the result."""
        ...


# == Implementation ==

log = logging.getLogger("vitality_observer")


class ObserveLoop(Loop):
    """The loop over a `config`'s captures, calling `observer`; times are in seconds (`sleep`, `clock`: for tests)."""

    def __init__(self, config: ObserverConfig, observer: Observer,
                 sleep: Callable[[float], Awaitable[object]] = asyncio.sleep,
                 clock: Callable[[], float] = time.time,
                 rescan_seconds: float = 60, retry_initial: float = 5, retry_max: float = 300,
                 usage_wait: float = 1800, usage_wait_min: float = 60, max_attempts: int = 3):
        self._config = config
        self._observer = observer
        self._sleep = sleep
        self._clock = clock
        self._rescan_seconds = rescan_seconds
        self._retry_initial = retry_initial
        self._retry_max = retry_max
        self._usage_wait = usage_wait
        self._usage_wait_min = usage_wait_min
        self._max_attempts = max_attempts
        self._skipped_path = config.state_dir / "skipped.txt"
        self._woken = asyncio.Event()
        self._strikes: dict[str, int] = {}
        self._backoff = retry_initial       # the next sleep after a failure
        self._alone = False                 # the last call failed: the next batch is one interaction
        self._limit_wait: float | None = None   # set by a step that hit a usage limit
        self._logged_failure: str | None = None     # the last failure logged since a recovery or idle round
        self._limited = False               # logged the start of a usage-limit wait

    def wake(self) -> None:
        self._woken.set()

    async def run(self) -> None:
        while True:
            try:
                step = await self.step()
            except (OSError, ValueError) as e:
                self._log_failure(f"{type(e).__name__}: {e}", error=True)
                step = "failed"
            if step == "idle":
                try:
                    await asyncio.wait_for(self._woken.wait(), self._rescan_seconds)
                except TimeoutError:
                    pass
            elif step == "failed":
                if self._limit_wait is not None:
                    await self._sleep(self._limit_wait)
                else:
                    await self._sleep(self._backoff)
                    self._backoff = min(self._backoff * 2, self._retry_max)

    async def step(self) -> Step:
        self._woken.clear()     # before scanning, so a wake() during the call is not lost
        self._limit_wait = None
        skipped = read_skipped(self._skipped_path)
        batch = self._front_batch(unobserved(self._config, skipped))
        if not batch:
            self._start_over()
            return "idle"
        result = await self._observer.observe(batch, context(self._config, batch[0]))

        still_waiting = {i.name for i in unobserved(self._config, skipped)}
        written = [i for i in batch if i.name not in still_waiting]
        if written:
            for interaction in written:
                self._strikes.pop(interaction.name, None)
            if self._logged_failure is not None or self._limited:
                log.info("observing works again")
            log.info("observed %d interaction(s), from %s", len(written), written[0].name)
            self._start_over()
            self._alone = False
            return "observed"
        if result.usage_limited:
            self._enter_usage_limit(result.usage_limit_reset)
            return "failed"
        reason = result.detail or "no observation written"
        self._log_failure(reason)
        self._alone = True
        name = batch[0].name
        if len(batch) == 1 and result.reached_model:
            strikes = self._strikes[name] = self._strikes.get(name, 0) + 1
            if strikes >= self._max_attempts:     # the count stays if the write fails, so it is retried
                add_skipped(self._skipped_path, name, reason)
                del self._strikes[name]
                log.warning("skipping %s after %d strikes: %s", name, strikes, reason)
        return "failed"

    def _front_batch(self, waiting: Sequence[Interaction]) -> list[Interaction]:
        """The oldest interaction and those after it from its device, up to `batch_size` (1 if `_alone`)."""
        size = 1 if self._alone else self._config.batch_size
        batch: list[Interaction] = []
        for interaction in waiting[:size]:
            if device_of(interaction.name) != device_of(waiting[0].name):
                break
            batch.append(interaction)
        return batch

    def _start_over(self) -> None:
        """After an observed or idle round: the first backoff, and failures are logged afresh."""
        self._backoff = self._retry_initial
        self._limited = False
        self._logged_failure = None

    def _log_failure(self, reason: str, error: bool = False) -> None:
        """Log a failure, unless it is the one last logged; `error` adds the exception being handled."""
        if reason == self._logged_failure:
            return
        self._logged_failure = reason
        if error:
            log.exception("observing hit an error, retrying with backoff")
        else:
            log.warning("observing failed, retrying with backoff: %s", reason)

    def _enter_usage_limit(self, reset: float | None) -> None:
        now = self._clock()
        self._limit_wait = self._usage_wait if reset is None else max(self._usage_wait_min, reset - now)
        if not self._limited:
            self._limited = True
            until = datetime.fromtimestamp(now + self._limit_wait, UTC).isoformat(timespec="seconds")
            log.warning("usage limit reached, waiting until %s", until)
