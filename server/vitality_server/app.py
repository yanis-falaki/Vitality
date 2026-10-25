"""The server's HTTP routes. Same table as `vitality_protocol.routes`.

`POST /interactions`
    Request: multipart. Part `interaction` = `Interaction` JSON; one part `shots` per
    file, its filename = `Shot.file`.
    Reply: `201` `UploadReply(stored="new")`, `200` `UploadReply(stored="existing")`,
    or an `ErrorReply` with an error status.

`GET /health`
    Reply: `200` `Health`.

Every error is an `ErrorReply`:

    403  the sending device is not on this server's tailnet or not in `allowed_clients`
    409  an interaction with this name is stored with different content
    411  a request with a `Transfer-Encoding` header, or a POST without a `Content-Length`
    413  the request is larger than `MAX_UPLOAD_BYTES`: by its `Content-Length`, or, because
         a header can lie, by the bytes actually received
    422  the request is malformed, or the storage refused it as invalid
    500  anything unexpected (details are logged, not sent)
    507  the disk or the device's quota is full

The checks run in this order, before the body is parsed: size headers (411, 413), then
the sending device (403, `POST /interactions` only). `GET /health` is open to any tailnet
peer. The sending device is learned from Tailscale (`Identity`), not from the request.
Retries are safe: sending the same interaction again returns `200 existing` and changes
nothing.
"""

import errno
import logging
from collections.abc import Collection
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import Json
from starlette.datastructures import Headers
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from vitality_protocol.messages import ErrorReply, Health, Interaction, UploadReply
from vitality_protocol.routes import HEALTH_PATH, INTERACTIONS_PATH, MAX_UPLOAD_BYTES

from vitality_server.identity import Identity
from vitality_server.storage import ConflictingUpload, InteractionStore, InvalidUpload

log = logging.getLogger(__name__)

_ERROR_STATUSES = (403, 409, 411, 413, 422, 507)


class _TooLarge(Exception):
    """More body bytes arrived than MAX_UPLOAD_BYTES allows."""


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse(ErrorReply(error=message).model_dump(mode="json"), status_code=status)


def _too_large() -> JSONResponse:
    return _error(413, f"request larger than {MAX_UPLOAD_BYTES} bytes")


class _UploadGate:
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
            await _error(411, "Transfer-Encoding not accepted; send a Content-Length")(scope, receive, send)
            return
        if is_post:
            length = headers.get("content-length")
            if length is None:
                log.warning("rejected %s: no Content-Length", peer)
                await _error(411, "Content-Length required")(scope, receive, send)
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
                await _error(403, "device not allowed")(scope, receive, send)
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


def create_app(store: InteractionStore, identity: Identity,
               allowed_clients: Collection[str]) -> FastAPI:
    app = FastAPI(title="Vitality server")
    app.add_middleware(_UploadGate, identity=identity, allowed_clients=allowed_clients)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0]
        field = ".".join(str(part) for part in first["loc"][1:])
        message = f"{field}: {first['msg']}" if field else first["msg"]
        log.warning("rejected %s: %s", _peer(request), message)
        return _error(422, message)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return _error(exc.status_code, str(exc.detail))

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        log.error("failed %s %s", request.method, request.url.path, exc_info=exc)
        return _error(500, "internal server error")

    @app.post(INTERACTIONS_PATH, responses={s: {"model": ErrorReply} for s in _ERROR_STATUSES})
    async def post_interaction(request: Request, response: Response,
                               interaction: Annotated[Json[Interaction], Form()],
                               shots: Annotated[list[UploadFile], File()]) -> UploadReply:
        device: str = request.state.device  # set by _UploadGate, which already checked it
        files: dict[str, bytes] = {}
        for shot in shots:
            if not shot.filename or shot.filename in files:
                log.warning("rejected %s: shot parts need distinct file names", device)
                raise HTTPException(422, "each shot needs its own file name")
            files[shot.filename] = await shot.read()

        try:
            stored = await run_in_threadpool(store.put, device, interaction, files)
        except InvalidUpload as e:
            log.warning("rejected %s %s: %s", device, interaction.name, e)
            raise HTTPException(422, str(e)) from e
        except ConflictingUpload as e:
            log.error("rejected %s %s: %s", device, interaction.name, e)
            raise HTTPException(409, str(e)) from e
        except OSError as e:
            if e.errno not in (errno.ENOSPC, errno.EDQUOT):
                raise  # anything else is unexpected: the 500 handler logs it
            log.error("rejected %s %s: disk full", device, interaction.name)
            raise HTTPException(507, "server disk full") from e

        log.info("stored %s %s: %s", device, interaction.name, stored)
        response.status_code = 201 if stored == "new" else 200
        return UploadReply(stored=stored)

    @app.get(HEALTH_PATH)
    async def health() -> Health:
        return Health(disk_free_bytes=store.disk_free_bytes())

    return app


def _peer(request: Request) -> str:
    return request.client.host if request.client else "unknown client"
