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
from vitality_observer.work import Interaction, add_skipped, context, device_of, read_skipped, unobserved

Step = Literal["idle", "observed", "failed"]
"""What one round did: "idle" found nothing to do; "observed" made a call that succeeded
(though some files may still be missing); "failed" made a call that did not."""

log = logging.getLogger("vitality_observer")

NO_OBSERVATION = "no observation written"


class ObserveLoop:
    """Keeps every interaction observed, one Claude call at a time.

    `run()` repeats `step()` until cancelled. After "observed" it goes straight on; after
    "idle" it waits for `wake()` or `rescan_seconds`; after "failed" it sleeps (backoff, or
    until a usage limit resets) and `wake()` does not cut that short.

    A batch is the oldest unobserved interaction plus the ones after it from the same
    device, at most `config.batch_size`. After a failed call the next batch is the oldest
    interaction alone, until a call succeeds, so one bad interaction can be told from an outage.

    Failed attempts are counted in memory only, and only for an interaction that failed
    alone, or that a successful call left without an observation. At `max_attempts` it is added
    to `skipped.txt` and never offered again. Attempts stop counting once the backoff has
    reached `retry_max`: Claude is then probably down, which is not the interaction's fault.
    A usage-limit failure counts for nothing and waits at least `usage_wait_min`.
    """

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
        self._attempts: dict[str, tuple[int, str]] = {}     # name -> (failed attempts, last reason)
        self._backoff = retry_initial       # the next sleep after a failure
        self._alone = False                 # the last call failed: the next batch is one interaction
        self._wait_for_limit: float | None = None           # set by a step that hit a usage limit
        self._failing = False       # logged the start of a failure streak
        self._limited = False       # logged the start of a usage-limit wait

    def wake(self) -> None:
        """Tell the loop a new interaction arrived, so an idle `run()` looks at once."""
        self._woken.set()

    async def run(self) -> None:
        """Observe until cancelled. A failed call, or an OSError/ValueError from a round, is retried."""
        while True:
            try:
                outcome = await self.step()
            except (OSError, ValueError):
                self._enter_failure(None)
                self._wait_for_limit = None
                outcome = "failed"
            if outcome == "idle":
                await self._wait_for_wake()
            elif outcome == "failed":
                if self._wait_for_limit is not None:    # set by step() when the failure was a usage limit
                    await self._sleep(self._wait_for_limit)
                else:
                    await self._sleep(self._backoff)
                    self._backoff = min(self._backoff * 2, self._retry_max)

    async def step(self) -> Step:
        """One round: pick a batch, make one Claude call, account for the result."""
        self._woken.clear()     # before scanning, so a wake() during the call is not lost
        self._wait_for_limit = None
        skipped = read_skipped(self._skipped_path)
        waiting = unobserved(self._config, skipped)
        if not waiting:
            self._forget_failures()
            return "idle"
        batch = self._front_batch(waiting)
        result = await self._observer.observe(batch, context(self._config, batch[0]))

        if not result.ok:
            if result.usage_limited:
                self._enter_usage_limit(result.usage_limit_reset)
            else:
                self._enter_failure(result.detail)
                self._alone = True
                # A lone interaction that fails is suspect; a failed batch or a long outage proves nothing.
                if len(batch) == 1 and self._backoff < self._retry_max:
                    self._count_attempt(batch, result.detail or "call failed")
            return "failed"

        missing = {i.name for i in unobserved(self._config, skipped)}
        written = [i for i in batch if i.name not in missing]
        for interaction in written:
            self._attempts.pop(interaction.name, None)
        if written:
            if self._failing or self._limited:
                log.info("observing works again")
            self._failing = self._limited = False
            log.info("observed %d interaction(s), from %s", len(written), written[0].name)
        self._alone = False
        self._backoff = self._retry_initial
        self._count_attempt([i for i in batch if i.name in missing], NO_OBSERVATION)
        return "observed"

    # == Implementation ==

    def _front_batch(self, waiting: Sequence[Interaction]) -> list[Interaction]:
        """The oldest interaction and those right after it from the same device, up to `batch_size`.

        Only the oldest alone after a failed call.
        """
        size = 1 if self._alone else self._config.batch_size
        device = device_of(waiting[0].name)
        batch: list[Interaction] = []
        for interaction in waiting[:size]:
            if device_of(interaction.name) != device:
                break
            batch.append(interaction)
        return batch

    async def _wait_for_wake(self) -> None:
        try:
            await asyncio.wait_for(self._woken.wait(), self._rescan_seconds)
        except TimeoutError:
            pass

    def _forget_failures(self) -> None:
        """Back to a clean start, so a later failure starts at `retry_initial` and is logged again."""
        self._failing = self._limited = self._alone = False
        self._backoff = self._retry_initial

    def _enter_failure(self, detail: str | None) -> None:
        """Log the start of a failure streak; `detail` None means an exception is being handled."""
        if self._failing:
            return
        self._failing = True
        if detail is None:
            log.exception("observing hit an error, retrying with backoff")
        else:
            log.warning("observing failed, retrying with backoff: %s", detail)

    def _enter_usage_limit(self, reset: float | None) -> None:
        now = self._clock()
        if reset is None:
            self._wait_for_limit = self._usage_wait
        else:
            self._wait_for_limit = max(self._usage_wait_min, reset - now)
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
