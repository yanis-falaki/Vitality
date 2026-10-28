"""The observer's main loop: find unobserved interactions, hand them to Claude in batches, account for the outcome.

One Claude call at a time. Failures are retried with a growing pause; an interaction that
keeps failing is skipped (recorded in `skipped.txt`). A usage limit is waited out and never
counts against the interactions in the call.
"""

# == Contract ==

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from typing import Literal

from vitality_observer.claude import Observer
from vitality_observer.config import ObserverConfig
from vitality_observer.work import Interaction, add_skipped, context, read_skipped, unobserved

Step = Literal["idle", "observed", "failed"]
"""What one round did: "idle" found nothing to do; "observed" made a call that succeeded
(though some files may still be missing); "failed" made a call that did not."""


# == Implementation ==

log = logging.getLogger("vitality_observer")

NO_OBSERVATION = "no observation written"


class ObserveLoop:
    """Keeps every interaction observed, one Claude call at a time.

    `run()` repeats `step()` until cancelled. After "observed" it goes straight on; after
    "idle" it waits for `wake()` or `rescan_seconds`; after "failed" it sleeps (backoff, or
    until a usage limit resets) and `wake()` does not cut that short.

    A batch is the oldest unobserved interaction plus the ones after it from the same
    device, at most `config.batch_size`. An interaction in a failed call, or still missing
    after a successful one, gets an attempt counted (in memory only); at `max_attempts` it
    is added to `skipped.txt` and never offered again. A usage-limit failure counts for nothing.
    """

    def __init__(self, config: ObserverConfig, observer: Observer,
                 sleep: Callable[[float], Awaitable[object]] = asyncio.sleep,
                 clock: Callable[[], float] = time.time,
                 rescan_seconds: float = 60, retry_initial: float = 5, retry_max: float = 300,
                 usage_wait: float = 1800, max_attempts: int = 3):
        self._config = config
        self._observer = observer
        self._sleep = sleep
        self._clock = clock
        self._rescan_seconds = rescan_seconds
        self._retry_initial = retry_initial
        self._retry_max = retry_max
        self._usage_wait = usage_wait
        self._max_attempts = max_attempts
        self._skipped_path = config.state_dir / "skipped.txt"
        self._woken = asyncio.Event()
        self._attempts: dict[str, tuple[int, str]] = {}     # name -> (failed attempts, last reason)
        self._wait_for_limit: float | None = None           # set by a step that hit a usage limit
        self._failing = False       # logged the start of a failure streak
        self._limited = False       # logged the start of a usage-limit wait

    def wake(self) -> None:
        """Tell the loop a new interaction arrived, so an idle `run()` looks at once."""
        self._woken.set()

    async def run(self) -> None:
        """Observe until cancelled; the loop itself never ends on a failed call."""
        backoff = self._retry_initial
        while True:
            outcome = await self.step()
            if outcome == "observed":
                backoff = self._retry_initial
            elif outcome == "idle":
                await self._wait_for_wake()
            elif self._wait_for_limit is not None:
                await self._sleep(self._wait_for_limit)
            else:
                await self._sleep(backoff)
                backoff = min(backoff * 2, self._retry_max)

    async def step(self) -> Step:
        """One round: pick a batch, make one Claude call, account for the result."""
        self._woken.clear()     # before scanning, so a wake() during the call is not lost
        self._wait_for_limit = None
        skipped = read_skipped(self._skipped_path)
        waiting = unobserved(self._config, skipped)
        if not waiting:
            return "idle"
        batch = self._front_batch(waiting)
        result = await self._observer.observe(batch, context(self._config, batch[0]))

        if not result.ok:
            if result.usage_limited:
                self._enter_usage_limit(result.usage_limit_reset)
            else:
                self._enter_failure(result.detail)
                self._count_attempt(batch, result.detail or "call failed")
            return "failed"

        if self._failing or self._limited:
            log.info("observing works again")
            self._failing = self._limited = False
        missing = {i.name for i in unobserved(self._config, skipped)}
        written = [i for i in batch if i.name not in missing]
        if written:
            log.info("observed %d interaction(s), from %s", len(written), written[0].name)
        self._count_attempt([i for i in batch if i.name in missing], NO_OBSERVATION)
        return "observed"

    def _front_batch(self, waiting: Sequence[Interaction]) -> list[Interaction]:
        """The oldest interaction and those right after it from the same device, up to `batch_size`."""
        device = waiting[0].name.split("/")[0]
        batch: list[Interaction] = []
        for interaction in waiting[:self._config.batch_size]:
            if interaction.name.split("/")[0] != device:
                break
            batch.append(interaction)
        return batch

    async def _wait_for_wake(self) -> None:
        try:
            await asyncio.wait_for(self._woken.wait(), self._rescan_seconds)
        except TimeoutError:
            pass

    def _enter_failure(self, detail: str) -> None:
        if not self._failing:
            self._failing = True
            log.warning("observing failed, retrying with backoff: %s", detail)

    def _enter_usage_limit(self, reset: float | None) -> None:
        now = self._clock()
        self._wait_for_limit = max(0.0, reset - now) if reset is not None else self._usage_wait
        if not self._limited:
            self._limited = True
            until = datetime.fromtimestamp(now + self._wait_for_limit, UTC).isoformat(timespec="seconds")
            log.warning("usage limit reached, waiting until %s", until)

    def _count_attempt(self, failed: Sequence[Interaction], reason: str) -> None:
        """Count a failed attempt for each of `failed`; skip the ones that used up their attempts."""
        for interaction in failed:
            attempts = self._attempts.get(interaction.name, (0, ""))[0] + 1
            if attempts < self._max_attempts:
                self._attempts[interaction.name] = (attempts, reason)
                continue
            self._attempts.pop(interaction.name, None)
            add_skipped(self._skipped_path, interaction.name, reason)
            log.warning("skipping %s after %d failed attempts: %s", interaction.name, attempts, reason)
