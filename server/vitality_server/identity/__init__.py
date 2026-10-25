"""Which device sent a request. The server only accepts uploads from named devices.

Contract: `Identity` (interface.py).
Implementation: `TailscaleIdentity` (tailscale.py), which asks the local Tailscale daemon;
`tailscale_ipv4` finds the address the server should listen on.
"""

from vitality_server.identity.interface import Identity
from vitality_server.identity.tailscale import TailscaleIdentity, tailscale_ipv4

__all__ = ["Identity", "TailscaleIdentity", "tailscale_ipv4"]
