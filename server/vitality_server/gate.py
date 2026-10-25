"""The gate in front of the app: turns away a request before its body is parsed.

It is raw ASGI middleware, not a FastAPI dependency or `BaseHTTPMiddleware`, because it has
to act before FastAPI reads the body: refuse on the headers alone, and count the bytes as
the app reads them so a body bigger than it declared is cut short. Anything built on the
request object would already have parsed (and buffered) the upload.
"""

import logging
from collections.abc import Collection

from fastapi.responses import JSONResponse
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from vitality_protocol.routes import INTERACTIONS_PATH, MAX_UPLOAD_BYTES

from vitality_server.errors import error
from vitality_server.identity import Identity

log = logging.getLogger(__name__)


class _TooLarge(Exception):
    """More body bytes arrived than MAX_UPLOAD_BYTES allows."""


def _too_large() -> JSONResponse:
    return error(413, f"request larger than {MAX_UPLOAD_BYTES} bytes")


class UploadGate:
    """Turns away a request before its body is parsed.

    In order: a `Transfer-Encoding` header (411: the server frames bodies by
    `Content-Length` only); a POST without a valid `Content-Length` (411 / 413) or with one
    over the cap (413); then, for `POST /interactions`, a device that is not allowed (403),
    whose name is left in `request.state.device` for the route. While the app reads the
    body the bytes are counted, because a `Content-Length` can disagree with what is sent:
    past the cap the read is cut short and the reply becomes 413.
    """

    def __init__(self, app: ASGIApp, identity: Identity, allowed_clients: Collection[str]) -> None:
        self._app = app
        self._identity = identity
        self._allowed = allowed_clients

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        peer = scope["client"][0] if scope.get("client") else "unknown client"
        is_post = scope["method"] == "POST"

        if "transfer-encoding" in headers:
            log.warning("rejected %s: Transfer-Encoding is not accepted", peer)
            await error(411, "Transfer-Encoding not accepted; send a Content-Length")(scope, receive, send)
            return
        if is_post:
            length = headers.get("content-length")
            if length is None:
                log.warning("rejected %s: no Content-Length", peer)
                await error(411, "Content-Length required")(scope, receive, send)
                return
            if not (length.isascii() and length.isdecimal()) or int(length) > MAX_UPLOAD_BYTES:
                log.warning("rejected %s: request too large (Content-Length %r)", peer, length)
                await _too_large()(scope, receive, send)
                return
        if is_post and scope["path"] == INTERACTIONS_PATH:
            device = await self._identity.who(peer) if scope.get("client") else None
            if device is None or device not in self._allowed:
                reason = "unknown to Tailscale" if device is None else "not in allowed_clients"
                log.warning("rejected %s: device not allowed (%s)", device or peer, reason)
                await error(403, "device not allowed")(scope, receive, send)
                return
            scope.setdefault("state", {})["device"] = device

        received = 0
        exceeded = False
        replaced = False

        async def counting_receive() -> Message:
            nonlocal received, exceeded
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > MAX_UPLOAD_BYTES:
                    exceeded = True
                    raise _TooLarge
            return message

        async def guarded_send(message: Message) -> None:
            # FastAPI turns the failed read into its own 400; swap in the 413 instead.
            nonlocal replaced
            if replaced:
                return
            if exceeded and message["type"] == "http.response.start":
                replaced = True
                await _too_large()(scope, receive, send)
                return
            await send(message)

        try:
            await self._app(scope, counting_receive, guarded_send)
        except _TooLarge:
            pass
        if exceeded:
            log.warning("rejected %s: more than %d bytes received", peer, MAX_UPLOAD_BYTES)
            if not replaced:
                await _too_large()(scope, receive, send)
