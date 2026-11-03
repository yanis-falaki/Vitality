"""The observer's main loop: find unobserved interactions, hand them to the provider in rounds, account for each outcome."""

# == Contract ==

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Literal, Protocol

from vitality_observer.config import ObserverConfig
from vitality_observer.provider.interface import Observer, Outcome
from vitality_observer.work import add_skipped, read_skipped, unobserved

BATCH_LIMIT = 100
"""Most interactions in one round in batch mode (one Message Batch)."""

Step = Literal["idle", "observed", "failed"]
"""What one round did.
"idle": nothing was waiting, so no call was made.
"observed": at least one interaction of the round is observed afterwards (both files on disk),
whatever the outcomes say.
"failed": none is, even if some outcome is `ok`."""


class Loop(Protocol):
    """Keeps every interaction observed, one `observe` call at a time, by five rules.

    Parameters named below belong to `ObserveLoop`.

    1. Round. The oldest waiting interactions across devices (by `chronological_key`), at most
       `config.parallel` in direct mode or `BATCH_LIMIT` (100) in batch mode, go in one
       `observe` call. Devices are not grouped and nothing is given as context.
    2. Judge by the disk. The interactions observed afterwards (both files written) count as
       written. Any written: the round is "observed" and the backoff resets.
    3. Wait. Nothing written and some outcome has `wait_seconds` (the account, not the
       interaction, is the problem): sleep the largest of them, leaving the backoff as it was.
       Logged once, with its end, until a round writes again. Those outcomes earn no strike.
    4. Back off. Nothing written otherwise: the round is "failed"; sleep `retry_initial` seconds
       (5), doubling each time up to `retry_max` (300). An observed or idle round resets it.
       `wake()` cuts neither this nor a wait short.
    5. Strike. Each interaction whose own outcome failed with `reached_model` (and no
       `wait_seconds`) and that is still unobserved gets a strike, whether or not others in the
       round were written. Strikes are kept in memory and cleared when the interaction is
       written. At `max_attempts` (3) it is added to `skipped.txt` with that outcome's `detail`
       and never offered again; deleting its line offers it again. Nothing else strikes: a
       failure that did not reach the model, an `ok` outcome whose files are missing, or an
       error raised in a round.
    """

    def wake(self) -> None:
        """Tell the loop a new interaction arrived, so an idle `run()` looks at once."""
        ...

    async def run(self) -> None:
        """Repeat `step()` until cancelled.

        After "observed" it goes straight on; after "idle" it waits for `wake()` or
        `rescan_seconds`; after "failed" it sleeps as rule 3 or 4 says. An OSError or ValueError
        from a round (an unreadable `skipped.txt`, say) is a failed round for rule 4 only: no
        strike. It logs the start of a failure streak and each change of its detail, the start
        of a wait (with its end), recovery once, each skip, and each round that wrote something.
        """
        ...

    async def step(self) -> Step:
        """One round: pick the interactions (rule 1), make one `observe` call, apply rules 2, 3 and 5."""
        ...


# == Implementation ==

log = logging.getLogger("vitality_observer")


class ObserveLoop(Loop):
    """The loop over a `config`'s captures, calling `observer`; times are in seconds (`sleep`, `clock`: for tests)."""

    def __init__(self, config: ObserverConfig, observer: Observer,
                 sleep: Callable[[float], Awaitable[object]] = asyncio.sleep,
                 clock: Callable[[], float] = time.time,
                 rescan_seconds: float = 60, retry_initial: float = 5, retry_max: float = 300,
                 max_attempts: int = 3):
        # Settings, kept as given. `sleep` and `clock` are asyncio.sleep and time.time in real use;
        # tests pass fakes so they can check the waits without actually waiting.
        self._config = config
        self._observer = observer
        self._sleep = sleep
        self._clock = clock
        self._rescan_seconds = rescan_seconds
        self._retry_initial = retry_initial
        self._retry_max = retry_max
        self._max_attempts = max_attempts
        self._skipped_path = config.state_dir / "skipped.txt"
        self._round_size = config.parallel if config.mode == "direct" else BATCH_LIMIT

        # What the loop remembers from one round to the next (in memory only; a restart forgets it).
        self._woken = asyncio.Event()       # the doorbell: wake() rings it, an idle run() waits on it
        self._strikes: dict[str, int] = {}  # interaction name -> strikes so far (rule 5)
        self._backoff = retry_initial       # the next sleep after a failure (rule 4)
        self._wait: float | None = None     # set by a step whose outcomes asked to wait: how long run() sleeps (rule 3)
        self._logged_failure: str | None = None     # the last failure logged since a recovery or idle round
        self._waiting = False               # logged the start of a wait since a recovery or idle round

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
                if self._wait is not None:
                    # The account must wait (rule 3): sleep that long; the backoff is left as it was.
                    await self._sleep(self._wait)
                else:
                    # Any other failure: sleep the backoff, then double it for next time (rule 4).
                    await self._sleep(self._backoff)
                    self._backoff = min(self._backoff * 2, self._retry_max)

    async def step(self) -> Step:
        # Silence the doorbell before scanning: a wake() while the call runs rings it again.
        self._woken.clear()
        self._wait = None

        # Pick the round (rule 1). skipped.txt is read every round, so deleting a line takes effect at once.
        skipped = read_skipped(self._skipped_path)
        batch = unobserved(self._config, skipped)[:self._round_size]
        if not batch:
            self._start_over()
            return "idle"

        # One call for the whole round.
        outcomes = await self._observer.observe(batch)

        # Judge by the disk, not by the outcomes (rule 2): which interactions are observed now?
        still_waiting = {i.name for i in unobserved(self._config, skipped)}
        written = [i for i in batch if i.name not in still_waiting]
        for interaction in written:
            self._strikes.pop(interaction.name, None)

        # Strike the interactions that failed by their own fault (rule 5), in any round.
        offered = {i.name for i in batch}
        for outcome in outcomes:
            if (outcome.name in offered and outcome.name in still_waiting and not outcome.ok
                    and outcome.reached_model and outcome.wait_seconds is None):
                self._strike(outcome)

        # Something was written: progress. The backoff resets.
        if written:
            if self._logged_failure is not None or self._waiting:
                log.info("observing works again")
            log.info("observed %d interaction(s), from %s", len(written), written[0].name)
            self._start_over()
            return "observed"

        # Nothing written, and the account must wait: run() sleeps the longest wait asked (rule 3).
        waits = [o for o in outcomes if not o.ok and o.wait_seconds is not None]
        if waits:
            longest = max(waits, key=lambda o: o.wait_seconds or 0)
            self._enter_wait(longest.wait_seconds or 0, longest.detail)
            return "failed"

        # Nothing written, any other reason: a failure, backed off by run() (rule 4).
        failures = [o.detail for o in outcomes if not o.ok and o.detail]
        self._log_failure(failures[0] if failures else "nothing written")
        return "failed"

    def _strike(self, outcome: Outcome) -> None:
        """Give `outcome`'s interaction a strike; at `max_attempts`, skip it with the outcome's detail."""
        name = outcome.name
        strikes = self._strikes.get(name, 0) + 1
        self._strikes[name] = strikes
        if strikes >= self._max_attempts:
            reason = outcome.detail or "failed"
            # Record the skip before forgetting the count: if the write fails, the count stays
            # and the skip is tried again next time.
            add_skipped(self._skipped_path, name, reason)
            del self._strikes[name]
            log.warning("skipping %s after %d strikes: %s", name, strikes, reason)

    def _start_over(self) -> None:
        """After an observed or idle round: the first backoff, and failures and waits are logged afresh."""
        self._backoff = self._retry_initial
        self._waiting = False
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

    def _enter_wait(self, seconds: float, detail: str) -> None:
        self._wait = seconds
        if not self._waiting:   # log the start of the wait once, not on every retry
            self._waiting = True
            until = datetime.fromtimestamp(self._clock() + seconds, UTC).isoformat(timespec="seconds")
            log.warning("%s, waiting until %s", detail or "asked to wait", until)
