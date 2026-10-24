"""The xdg-desktop-portal ScreenCast conversation over D-Bus.

The contract (`PortalSession`, `Stream`, the errors) comes first; the implementation
(`ScreenCastSession`) follows. A session asks the portal for every monitor (cursor
embedded, permission persisted) and hands back the PipeWire streams; with a valid
restore token the portal skips its permission dialog.

Never logged: the restore token value.
"""

import asyncio
import logging
import os
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from dbus_fast import BusType, Message, MessageType, Variant
from dbus_fast.aio import MessageBus


# == Contract =============================================================================

@dataclass(frozen=True)
class Stream:
    """One monitor's PipeWire stream; position and size are in logical pixels."""

    node_id: int
    position: tuple[int, int]
    size: tuple[int, int] | None  # None when the portal did not report it


class PortalSession(Protocol):
    """One screen-capture session with the portal (what PortalFrameSource uses)."""

    fd: int | None
    """The PipeWire connection to read the streams from, once `open` has returned."""

    async def open(
        self, restore_token: str | None, on_restore_token: Callable[[str], None],
    ) -> list[Stream]:
        """Ask the portal for every monitor and return their streams.

        Shows the permission dialog unless `restore_token` is still valid. Tokens are
        single-use: the portal's new one goes to `on_restore_token` the moment it arrives,
        before anything else can fail.
        """
        ...

    def lost(self) -> str | None:
        """Why the session stopped being usable (portal closed it, bus lost), or None."""
        ...

    async def close(self) -> None:
        """End the session and release the PipeWire connection."""
        ...


RESPONSE_CANCELLED = 1  # the user cancelled the permission dialog
_RESPONSE_MEANING = {RESPONSE_CANCELLED: "cancelled by the user", 2: "ended by the portal"}


class PortalError(Exception):
    """Talking to the portal failed."""


class PortalResponseError(PortalError):
    """The portal answered a request with a non-zero response code."""

    def __init__(self, method: str, response: int) -> None:
        meaning = _RESPONSE_MEANING.get(response, f"response {response}")
        super().__init__(f"portal {method} {meaning}")
        self.response = response


# == Implementation =======================================================================

log = logging.getLogger(__name__)

PORTAL_BUS_NAME = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
SCREENCAST_IFACE = "org.freedesktop.portal.ScreenCast"
REQUEST_IFACE = "org.freedesktop.portal.Request"
SESSION_IFACE = "org.freedesktop.portal.Session"

SOURCE_TYPE_MONITOR = 1
CURSOR_MODE_EMBEDDED = 2
PERSIST_MODE_UNTIL_REVOKED = 2

_CLOSE_TIMEOUT = 2.0


def request_path(unique_name: str, handle_token: str) -> str:
    """Object path of the Request a portal call with this handle_token will create."""
    sender = unique_name.lstrip(":").replace(".", "_")
    return f"{PORTAL_PATH}/request/{sender}/{handle_token}"


class ScreenCastSession(PortalSession):
    def __init__(self) -> None:
        self.fd: int | None = None
        self._bus: MessageBus | None = None
        self._session: str | None = None
        self._closed_by_portal = False

    async def open(
        self, restore_token: str | None, on_restore_token: Callable[[str], None],
    ) -> list[Stream]:
        """Start a session and return its streams."""
        self._closed_by_portal = False
        self._bus = bus = await MessageBus(
            bus_type=BusType.SESSION, negotiate_unix_fd=True
        ).connect()

        results = await self._request(
            "CreateSession", "a{sv}",
            [{"session_handle_token": Variant("s", _new_token())}],
        )
        self._session = session = results["session_handle"].value
        await self._watch_closed(session)

        options = {
            "types": Variant("u", SOURCE_TYPE_MONITOR),
            "multiple": Variant("b", True),
            "cursor_mode": Variant("u", CURSOR_MODE_EMBEDDED),
            "persist_mode": Variant("u", PERSIST_MODE_UNTIL_REVOKED),
        }
        if restore_token:
            options["restore_token"] = Variant("s", restore_token)
        await self._request("SelectSources", "oa{sv}", [session, options])

        results = await self._request("Start", "osa{sv}", [session, "", {}])
        if "restore_token" in results:
            on_restore_token(results["restore_token"].value)
        streams = [_stream(node_id, props) for node_id, props in
                   (results["streams"].value if "streams" in results else [])]
        if not streams:
            raise PortalError("portal Start returned no streams")

        reply = await bus.call(Message(
            destination=PORTAL_BUS_NAME, path=PORTAL_PATH, interface=SCREENCAST_IFACE,
            member="OpenPipeWireRemote", signature="oa{sv}", body=[session, {}],
        ))
        _check_reply(reply, "OpenPipeWireRemote")
        self.fd = reply.unix_fds[reply.body[0]]
        return streams

    def lost(self) -> str | None:
        """Why the session is no longer usable, or None while it is."""
        if self._closed_by_portal:
            return "portal session closed"
        if self._bus is None or not self._bus.connected:
            return "D-Bus connection lost"
        return None

    async def close(self) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        bus, self._bus = self._bus, None
        session, self._session = self._session, None
        if bus is None:
            return
        if session is not None and bus.connected:
            try:
                await asyncio.wait_for(bus.call(Message(
                    destination=PORTAL_BUS_NAME, path=session,
                    interface=SESSION_IFACE, member="Close",
                )), _CLOSE_TIMEOUT)
            except Exception as exc:
                log.debug("closing the portal session failed: %s", _describe(exc))
        bus.disconnect()

    async def _request(self, method: str, signature: str, body: list) -> dict[str, Variant]:
        """Call a ScreenCast method and wait for its Request.Response; return the results."""
        bus = self._connected_bus()
        handle_token = _new_token()
        body[-1]["handle_token"] = Variant("s", handle_token)
        path = request_path(bus.unique_name or "", handle_token)
        rule = f"type='signal',interface='{REQUEST_IFACE}',member='Response',path='{path}'"
        response: asyncio.Future = asyncio.get_running_loop().create_future()

        def on_message(msg: Message) -> None:
            if (msg.message_type == MessageType.SIGNAL and msg.path == path
                    and msg.interface == REQUEST_IFACE and msg.member == "Response"
                    and not response.done()):
                response.set_result(msg.body)

        # Subscribe before calling so a fast Response cannot be missed.
        bus.add_message_handler(on_message)
        try:
            await _bus_match(bus, "AddMatch", rule)
            reply = await bus.call(Message(
                destination=PORTAL_BUS_NAME, path=PORTAL_PATH, interface=SCREENCAST_IFACE,
                member=method, signature=signature, body=body,
            ))
            _check_reply(reply, method)
            if reply.body[0] != path:
                raise PortalError(f"portal {method} returned an unexpected request path")
            code, results = await response
        finally:
            bus.remove_message_handler(on_message)
            if bus.connected:
                try:
                    await _bus_match(bus, "RemoveMatch", rule)
                except Exception:
                    pass
        if code != 0:
            raise PortalResponseError(method, code)
        return results

    def _connected_bus(self) -> MessageBus:
        if self._bus is None:
            raise PortalError("not connected to the session bus")
        return self._bus

    async def _watch_closed(self, session: str) -> None:
        def on_message(msg: Message) -> None:
            if (msg.message_type == MessageType.SIGNAL and msg.path == session
                    and msg.interface == SESSION_IFACE and msg.member == "Closed"):
                self._closed_by_portal = True

        bus = self._connected_bus()
        bus.add_message_handler(on_message)
        await _bus_match(
            bus, "AddMatch",
            f"type='signal',interface='{SESSION_IFACE}',member='Closed',path='{session}'",
        )


def _stream(node_id: int, props: dict) -> Stream:
    pos = props["position"].value if "position" in props else (0, 0)
    size = props["size"].value if "size" in props else None
    return Stream(
        node_id=int(node_id),
        position=(int(pos[0]), int(pos[1])),
        size=(int(size[0]), int(size[1])) if size and min(size) > 0 else None,
    )


async def _bus_match(bus: MessageBus, member: str, rule: str) -> None:
    reply = await bus.call(Message(
        destination="org.freedesktop.DBus", path="/org/freedesktop/DBus",
        interface="org.freedesktop.DBus", member=member, signature="s", body=[rule],
    ))
    _check_reply(reply, member)


def _check_reply(reply: Message, method: str) -> None:
    if reply.message_type == MessageType.ERROR:
        raise PortalError(f"{method} failed: {reply.error_name}")


def _new_token() -> str:
    return f"vitality_{secrets.token_hex(8)}"


def _describe(exc: BaseException) -> str:
    return str(exc) or type(exc).__name__
