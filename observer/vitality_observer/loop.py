"""The observer's main loop: find unobserved interactions, hand them to Claude in batches, account for the outcome.

One Claude call at a time. Failures are retried with a growing pause. An interaction that
fails alone after reaching Claude is blamed only once another call shows that Claude works;
one blamed `max_attempts` times is skipped (recorded in `skipped.txt`). A usage limit is
waited out and never counts against anyone.
"""

# == Contract ==

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from typing import Literal, NamedTuple, Protocol

from vitality_observer.claude import Observer
from vitality_observer.config import ObserverConfig
from vitality_observer.work import Interaction, add_skipped, context, device_of, read_skipped, unobserved

Step = Literal["idle", "observed", "failed"]
"""What one round did: "idle" made no call; "observed" made a call that wrote at least one
observation (some may still be missing, and Claude may have reported an error); "failed" made
a call that wrote none."""


class Loop(Protocol):
    """Keeps every interaction observed, one Claude call at a time.

    The names `batch_size` (a setting) and `max_attempts`, `retry_initial`, `retry_max`,
    `usage_wait_min` (parameters of `ObserveLoop`) are explained there.

    Batches. A batch is the oldest unobserved interaction plus the ones right after it from
    the same device, at most `batch_size`. After a failed call the next batch is the oldest
    interaction alone, until a call succeeds, so one bad interaction can be told from an
    outage.

    Success. A call succeeds when it writes at least one of its observations, even if Claude
    reported an error (but not a usage limit, below); a call that writes none fails. A
    success proves that Claude works.

    Attempts. Failed attempts are counted in memory only. An interaction gets one when
      - a call that succeeded left it without an observation (Claude worked, so it is at
        fault), or
      - it is the suspect when a call succeeds (below).
    At `max_attempts` it is added to `skipped.txt` and never offered again.

    The suspect. When a call for one interaction alone fails after reaching Claude (the
    result says `reached_model`; a timeout counts), that interaction becomes the suspect: the
    fault is its own or Claude's, and the loop must see Claude work before blaming it. There
    is at most one suspect; while there is one, other failures make no new one. While there
    is a suspect every call is a probe: the oldest waiting interaction that is not the
    suspect, alone, passing over any that already failed as a probe until all of them have.
    The first call that succeeds gives the suspect one failed attempt (its last failure is
    the reason) and clears it; then full batches resume. So a bad interaction is skipped
    after three such cycles, and an outage, however long, costs only the oldest interaction,
    and only one attempt. While the suspect is the only interaction waiting, no call is made;
    the next interaction to arrive becomes the probe.

    Never counted, and never making a suspect: a failure that did not reach Claude (a
    missing `claude`, a logged-out account, a network that fails at once; a hang times out,
    and a timeout does count as reaching Claude), a usage limit (waited out until its
    reported reset, but at least `usage_wait_min`, even if the call wrote some files), and
    an error raised in a round.
    """

    def wake(self) -> None:
        """Tell the loop a new interaction arrived, so an idle `run()` looks at once."""
        ...

    async def run(self) -> None:
        """Repeat `step()` until cancelled.

        After "observed" it goes straight on; after "idle" it waits for `wake()` or
        `rescan_seconds`; after "failed" it sleeps, and `wake()` does not cut that short. The
        sleep is the backoff (from `retry_initial`, doubling up to `retry_max`; back to the
        start after a success or an idle round), or until a usage limit resets. An OSError or
        ValueError from a round backs off like a failed call, but changes neither the next
        batch nor any attempt. A failure is logged when its reason differs from the last one
        logged since the last success or idle round.
        """
        ...

    async def step(self) -> Step:
        """One round: pick a batch (or probe), make one Claude call, account for the result."""
        ...


# == Implementation ==

log = logging.getLogger("vitality_observer")

NO_OBSERVATION = "no observation written"


class _Suspect(NamedTuple):
    interaction: Interaction
    reason: str         # its last failure, the reason given if it is blamed


class ObserveLoop(Loop):
    """The loop over a `config`'s captures, calling `observer`; times are in seconds.

    `sleep` and `clock` can be replaced in tests. `rescan_seconds` is the longest an idle
    `run()` waits before looking again. The error backoff starts at `retry_initial` and
    doubles up to `retry_max`. A usage limit waits until its reported reset, but at least
    `usage_wait_min`, or `usage_wait` when none was reported. An interaction is skipped
    after `max_attempts` failed attempts.
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
        self._suspect: _Suspect | None = None
        self._failed_probes: set[str] = set()   # names that failed as a probe of the current suspect
        self._backoff = retry_initial       # the next sleep after a failure
        self._alone = False                 # the last call failed: the next batch is one interaction
        self._wait_for_limit: float | None = None           # set by a step that hit a usage limit
        self._logged_failure: str | None = None     # the last failure logged since a success or idle round
        self._limited = False       # logged the start of a usage-limit wait

    def wake(self) -> None:
        self._woken.set()

    async def run(self) -> None:
        while True:
            try:
                outcome = await self.step()
            except (OSError, ValueError) as e:
                self._log_failure(f"{type(e).__name__}: {e}", error=True)
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
        self._woken.clear()     # before scanning, so a wake() during the call is not lost
        self._wait_for_limit = None
        skipped = read_skipped(self._skipped_path)
        waiting = unobserved(self._config, skipped)
        self._drop_suspect_if_gone(waiting)
        batch = self._probe(waiting) if self._suspect else self._front_batch(waiting)
        if not batch:
            self._forget_failures()
            return "idle"
        result = await self._observer.observe(batch, context(self._config, batch[0]))

        if result.usage_limited:
            self._enter_usage_limit(result.usage_limit_reset)
            return "failed"
        reason = result.detail or NO_OBSERVATION
        still_waiting = {i.name for i in unobserved(self._config, skipped)}
        written = [i for i in batch if i.name not in still_waiting]
        if not written:
            self._call_failed(batch, reason, result.reached_model)
            return "failed"
        self._call_worked(written, [i for i in batch if i.name in still_waiting], reason)
        return "observed"

    def _front_batch(self, waiting: Sequence[Interaction]) -> list[Interaction]:
        """The oldest interaction and those right after it from the same device, up to `batch_size`.

        Only the oldest alone after a failed call; empty if nothing waits.
        """
        size = 1 if self._alone else self._config.batch_size
        batch: list[Interaction] = []
        for interaction in waiting[:size]:
            if device_of(interaction.name) != device_of(waiting[0].name):
                break
            batch.append(interaction)
        return batch

    def _probe(self, waiting: Sequence[Interaction]) -> list[Interaction]:
        """The oldest waiting interaction other than the suspect, alone; empty if only the suspect waits.

        Passes over those that already failed as a probe, until all of them have.
        """
        assert self._suspect is not None
        others = [i for i in waiting if i.name != self._suspect.interaction.name]
        untried = [i for i in others if i.name not in self._failed_probes]
        if not untried:
            self._failed_probes.clear()
            untried = others
        return untried[:1]

    def _call_failed(self, batch: Sequence[Interaction], reason: str, reached_model: bool) -> None:
        """A call wrote nothing: the next batch is one interaction, and a lone one that reached Claude is suspect."""
        self._log_failure(reason)
        self._alone = True
        if self._suspect:
            self._failed_probes.add(batch[0].name)      # a probe is always alone
        elif reached_model and len(batch) == 1:
            self._suspect = _Suspect(batch[0], reason)
            log.warning("%s failed alone: suspect, but blamed only once a call for another interaction works",
                        batch[0].name)

    def _call_worked(self, written: Sequence[Interaction], missing: Sequence[Interaction], reason: str) -> None:
        """A call wrote some observations, so Claude works: blame the suspect and the `missing`, start afresh."""
        for interaction in written:
            self._attempts.pop(interaction.name, None)
        if self._logged_failure is not None or self._limited:
            log.info("observing works again")
        log.info("observed %d interaction(s), from %s", len(written), written[0].name)
        self._forget_failures()
        self._blame_suspect(proof=written[0])
        self._count_attempt(missing, reason)

    def _blame_suspect(self, proof: Interaction) -> None:
        """Count one failed attempt against the suspect, if there is one, and clear it."""
        if self._suspect is None:
            return
        suspect, self._suspect = self._suspect, None
        self._failed_probes.clear()
        log.warning("%s was observed, so Claude works: counting a failed attempt against %s",
                    proof.name, suspect.interaction.name)
        self._count_attempt([suspect.interaction], suspect.reason)

    def _drop_suspect_if_gone(self, waiting: Sequence[Interaction]) -> None:
        """Forget a suspect that no longer waits (observed or skipped by hand), blaming nothing."""
        if self._suspect and all(i.name != self._suspect.interaction.name for i in waiting):
            self._suspect = None
            self._failed_probes.clear()

    async def _wait_for_wake(self) -> None:
        try:
            await asyncio.wait_for(self._woken.wait(), self._rescan_seconds)
        except TimeoutError:
            pass

    def _forget_failures(self) -> None:
        """Back to a clean start, so a later failure starts at `retry_initial` and is logged again.

        The suspect is kept: it is cleared only by a success, or when it no longer waits.
        """
        self._logged_failure = None
        self._limited = self._alone = False
        self._backoff = self._retry_initial

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
        if reset is None:
            self._wait_for_limit = self._usage_wait
        else:
            self._wait_for_limit = max(self._usage_wait_min, reset - now)
        if not self._limited:
            self._limited = True
            until = datetime.fromtimestamp(now + self._wait_for_limit, UTC).isoformat(timespec="seconds")
            log.warning("usage limit reached, waiting until %s", until)

    def _count_attempt(self, failed: Sequence[Interaction], reason: str) -> None:
        """Count a failed attempt for each of `failed`; skip the ones that used up their attempts.

        The count is kept until the skip is written, so a failed write is retried at the next attempt.
        """
        for interaction in failed:
            attempts = self._attempts.get(interaction.name, (0, ""))[0] + 1
            self._attempts[interaction.name] = (attempts, reason)
            if attempts < self._max_attempts:
                continue
            add_skipped(self._skipped_path, interaction.name, reason)
            self._attempts.pop(interaction.name, None)
            log.warning("skipping %s after %d failed attempts: %s", interaction.name, attempts, reason)
