"""The contract for finding out which device sent a request."""

from typing import Protocol


class Identity(Protocol):
    """Names the device behind a network address."""

    async def who(self, ip: str) -> str | None:
        """The device name for this address (a Tailscale device name such as "laptop"),
        or None if the address belongs to no device on this server's own tailnet (unknown,
        or shared in from another tailnet) or the lookup failed. Never raises."""
        ...
