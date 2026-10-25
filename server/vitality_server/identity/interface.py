"""The contract for finding out which device sent a request."""

from typing import Protocol


class Identity(Protocol):
    """Names the device behind a network address."""

    async def who(self, ip: str) -> str | None:
        """The device name for this address (a Tailscale device name such as "laptop"),
        or None if the address belongs to no known device or the lookup failed."""
        ...
