"""What the loop asks of a model provider: observe a batch of interactions, one outcome each."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from vitality_observer.work import WorkItem


@dataclass(frozen=True)
class Outcome:
    """How observing one interaction went."""

    name: str                   # the interaction's name
    ok: bool                    # both its files were written
    reached_model: bool         # a failure is this interaction's own (the model worked on it, or it is bad), so it may earn a strike
    detail: str                 # short reason on failure; "" on success
    wait_seconds: float | None  # set when the account, not the interaction, is the problem: wait this long before calling again
    record: dict                # the line appended to calls.jsonl


class Observer(Protocol):
    async def observe(self, batch: Sequence[WorkItem]) -> list[Outcome]:
        """Observe every interaction in `batch`; never raises.

        Returns one outcome per interaction, in the order of `batch`. On success the
        interaction's observation and operations files are written; on failure neither is
        written by this call. Appends one line per interaction to `<state_dir>/calls.jsonl`
        (if that file cannot be written, a warning is logged instead and the call goes on).
        """
        ...
