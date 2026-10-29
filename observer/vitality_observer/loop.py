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
        # Settings, kept as given. `sleep` and `clock` are asyncio.sleep and time.time in real use;
        # tests pass fakes so they can check the waits without actually waiting.
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

        # What the loop remembers from one round to the next (in memory only; a restart forgets it).
        self._woken = asyncio.Event()       # the doorbell: wake() rings it, an idle run() waits on it
        self._strikes: dict[str, int] = {}  # interaction name -> strikes so far (rule 3)
        self._backoff = retry_initial       # the next sleep after a failure (rule 1)
        self._alone = False                 # the last call failed: the next batch is one interaction (rule 2)
        self._limit_wait: float | None = None   # set by a step that hit a usage limit: how long run() waits
        self._logged_failure: str | None = None     # the last failure logged since a recovery or idle round
        self._limited = False               # logged the start of a usage-limit wait

    def wake(self) -> None:
        self._woken.set()

    async def run(self) -> None:
        while True:     # until the service is stopped, which cancels this from outside
            # One round. A file problem in it (an unreadable skipped.txt, say) is a failed round,
            # retried with backoff, rather than a crash.
            try:
                step = await self.step()
            except (OSError, ValueError) as e:
                self._log_failure(f"{type(e).__name__}: {e}", error=True)
                step = "failed"

            # Then wait, depending on how the round went. After "observed", no wait: there may be more.
            if step == "idle":
                # Nothing waiting: sleep until wake() rings or rescan_seconds pass, whichever is first.
                try:
                    await asyncio.wait_for(self._woken.wait(), self._rescan_seconds)
                except TimeoutError:
                    pass
            elif step == "failed":
                if self._limit_wait is not None:
                    # A usage limit: wait it out; the backoff is left as it was.
                    await self._sleep(self._limit_wait)
                else:
                    # Any other failure: sleep the backoff, then double it for next time (rule 1).
                    await self._sleep(self._backoff)
                    self._backoff = min(self._backoff * 2, self._retry_max)

    async def step(self) -> Step:
        # Silence the doorbell before scanning: a wake() while the call runs rings it again.
        self._woken.clear()
        self._limit_wait = None

        # Pick the batch. skipped.txt is read every round, so deleting a line takes effect at once.
        skipped = read_skipped(self._skipped_path)
        batch = self._front_batch(unobserved(self._config, skipped))
        if not batch:
            self._start_over()
            return "idle"

        # One Claude call, with the earlier observations of the batch's device as context.
        result = await self._observer.observe(batch, context(self._config, batch[0]))

        # Judge by the disk, not by what Claude said: which observation files exist now?
        still_waiting = {i.name for i in unobserved(self._config, skipped)}
        written = [i for i in batch if i.name not in still_waiting]

        # Something was written: progress. Their strikes go, the backoff resets, full batches resume.
        if written:
            for interaction in written:
                self._strikes.pop(interaction.name, None)
            if self._logged_failure is not None or self._limited:
                log.info("observing works again")
            log.info("observed %d interaction(s), from %s", len(written), written[0].name)
            self._start_over()
            self._alone = False
            return "observed"

        # Nothing written because of a usage limit: wait it out; no strike, no isolation.
        if result.usage_limited:
            self._enter_usage_limit(result.usage_limit_reset)
            return "failed"

        # Nothing written, any other reason: a failure. The next batch is the oldest alone (rule 2).
        reason = result.detail or "no observation written"
        self._log_failure(reason)
        self._alone = True

        # A strike only if it was tried alone and Claude really worked on it (rule 3): a failed
        # batch of several, or Claude being unreachable, blames no one.
        name = batch[0].name
        if len(batch) == 1 and result.reached_model:
            strikes = self._strikes.get(name, 0) + 1
            self._strikes[name] = strikes
            if strikes >= self._max_attempts:
                # Record the skip before forgetting the count: if the write fails, the count stays
                # and the skip is tried again next time.
                add_skipped(self._skipped_path, name, reason)
                del self._strikes[name]
                log.warning("skipping %s after %d strikes: %s", name, strikes, reason)
        return "failed"

    def _front_batch(self, waiting: Sequence[Interaction]) -> list[Interaction]:
        """The oldest interaction and those after it from its device, up to `batch_size` (1 if `_alone`)."""
        size = 1 if self._alone else self._config.batch_size
        batch: list[Interaction] = []
        for interaction in waiting[:size]:
            # Stop at the first other device: the context given to Claude is per device.
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
        # Until the reported reset, but at least usage_wait_min (a reset already past would mean no
        # wait at all); usage_wait if Claude did not say when it resets.
        self._limit_wait = self._usage_wait if reset is None else max(self._usage_wait_min, reset - now)
        if not self._limited:   # log the start of the wait once, not on every retry
            self._limited = True
            until = datetime.fromtimestamp(now + self._limit_wait, UTC).isoformat(timespec="seconds")
            log.warning("usage limit reached, waiting until %s", until)
