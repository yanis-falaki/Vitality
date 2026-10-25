"""The gate in front of the app: turns a request away before its body is parsed.

It is raw ASGI middleware because it has to act before FastAPI reads the body: refuse on
the headers alone, and count the bytes as the app reads them. Anything built on the request
object would already have parsed (and buffered) the upload.
"""

import logging
from collections.abc import Collection
from typing import Protocol

from fastapi.responses import JSONResponse
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from vitality_protocol.routes import INTERACTIONS_PATH, MAX_UPLOAD_BYTES

from vitality_server.errors import error
from vitality_server.identity import Identity

log = logging.getLogger(__name__)


# == Contract =============================================================================

class RequestGate(Protocol):
    """Lets a request through to the app, or answers it itself without reading its body.

    A refusal is an `ErrorReply`:

        411  a `Transfer-Encoding` header, or a POST without a `Content-Length`
        413  a POST whose `Content-Length` is not a plain number or is over
             `MAX_UPLOAD_BYTES`, or whose body turns out bigger than that while it is read
        403  `POST /interactions` from a device Tailscale does not know, or one that is
             not in `allowed_clients`

    A request passed on to `POST /interactions` carries `request.state.device`, the
    sending device's name. The checks run in the order above.
    """

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Pass the request on to the app, or refuse it."""
        ...


# == Implementation =======================================================================

class UploadGate(RequestGate):
    def __init__(self, app: ASGIApp, identity: Identity, allowed_clients: Collection[str]) -> None:
        self._app = app
        self._identity = identity
        self._allowed = allowed_clients

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        peer = scope["client"][0] if scope.get("client") else "unknown client"
        is_post = scope["method"] == "POST"

        # 1. The size headers: refuse on the headers alone.
        if refusal := check_size_headers(Headers(scope=scope), peer, is_post):
            await refusal(scope, receive, send)
            return

        # 2. The device: only for POST /interactions, which then learns who sent it.
        if is_post and scope["path"] == INTERACTIONS_PATH:
            device = await self._allowed_device(scope, peer)
            if device is None:
                await error(403, "device not allowed")(scope, receive, send)
                return
            scope.setdefault("state", {})["device"] = device

        # 3. The body: run the app, cutting the read short if it exceeds the cap.
        await run_with_body_limit(self._app, scope, receive, send, peer)

    async def _allowed_device(self, scope: Scope, peer: str) -> str | None:
        """The sending device's name, or None (logged) if it is unknown or not allowed."""
        device = await self._identity.who(peer) if scope.get("client") else None
        if device is not None and device in self._allowed:
            return device
        reason = "unknown to Tailscale" if device is None else "not in allowed_clients"
        log.warning("rejected %s: device not allowed (%s)", device or peer, reason)
        return None


def check_size_headers(headers: Headers, peer: str, is_post: bool) -> JSONResponse | None:
    """The refusal (411 or 413, logged) if the headers rule the request out, else None."""
    if "transfer-encoding" in headers:
        log.warning("rejected %s: Transfer-Encoding is not accepted", peer)
        return error(411, "Transfer-Encoding not accepted; send a Content-Length")
    if not is_post:
        return None
    length = headers.get("content-length")
    if length is None:
        log.warning("rejected %s: no Content-Length", peer)
        return error(411, "Content-Length required")
    if not (length.isascii() and length.isdecimal()) or int(length) > MAX_UPLOAD_BYTES:
        log.warning("rejected %s: request too large (Content-Length %r)", peer, length)
        return _too_large()
    return None


class _TooLarge(Exception):
    """More body bytes arrived than MAX_UPLOAD_BYTES allows."""


def _too_large() -> JSONResponse:
    return error(413, f"request larger than {MAX_UPLOAD_BYTES} bytes")


async def run_with_body_limit(app: ASGIApp, scope: Scope, receive: Receive, send: Send,
                              peer: str) -> None:
    """Run `app`, answering 413 instead if its request body exceeds MAX_UPLOAD_BYTES."""
    received = 0
    exceeded = False
    replaced = False

    # A Content-Length header can lie, so count the bytes the app actually reads and cut
    # the read short once they pass the cap.
    async def counting_receive() -> Message:
        nonlocal received, exceeded
        message = await receive()
        if message["type"] == "http.request":
            received += len(message.get("body", b""))
            if received > MAX_UPLOAD_BYTES:
                exceeded = True
                raise _TooLarge
        return message

    # FastAPI turns the failed read into its own 400; swap in our 413 for it.
    async def guarded_send(message: Message) -> None:
        nonlocal replaced
        if replaced:
            return
        if exceeded and message["type"] == "http.response.start":
            replaced = True
            await _too_large()(scope, receive, send)
            return
        await send(message)

    try:
        await app(scope, counting_receive, guarded_send)
    except _TooLarge:
        pass
    if exceeded:
        log.warning("rejected %s: more than %d bytes received", peer, MAX_UPLOAD_BYTES)
        if not replaced:  # the app never started a reply, so there is none to swap
            await _too_large()(scope, receive, send)
