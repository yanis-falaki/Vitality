"""The contract for sending ended interactions to the server."""

from typing import Protocol

from vitality_client.shared.channel import UploadStatus


class Uploader(Protocol):
    """Uploads ended interactions from the capture index to the server, in the background."""

    async def run(self) -> None: ...     # until cancelled: upload pending interactions, oldest first
    def wake(self) -> None: ...          # an interaction just ended: check now
    @property
    def status(self) -> UploadStatus: ...
