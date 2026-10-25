"""The contract for sending ended interactions to the server."""

from typing import Protocol

from vitality_client.shared.channel import UploadStatus


class Uploader(Protocol):
    """Uploads ended interactions from the capture index to the server, in the background."""

    async def run(self) -> None:
        """Until cancelled: upload pending interactions, oldest first, retrying as needed.

        Never returns and raises nothing but `CancelledError`: any other error in a round is
        logged and treated as the server being unreachable (state `offline`, then backoff).
        """
        ...

    def wake(self) -> None:
        """An interaction just ended: stop waiting while idle and check now."""
        ...

    @property
    def status(self) -> UploadStatus:
        """The upload state, the number of pending interactions, and when the last upload
        succeeded (None if none has this run)."""
        ...
