"""The server's HTTP routes. Same table as `vitality_protocol.routes`.

`POST /interactions`
    Request: multipart. Part `interaction` = `Interaction` JSON; one part `shots` per
    file, its filename = `Shot.file`.
    Reply: `201` `UploadReply(stored="new")`, `200` `UploadReply(stored="existing")`,
    or an `ErrorReply` with an error status.

`GET /health`
    Reply: `200` `Health`.

Every error is an `ErrorReply`:

    403  the sending device is unknown to Tailscale or not in `allowed_clients`
    409  an interaction with this name is stored with different content
    411  a POST without a `Content-Length`
    413  the request is larger than `MAX_UPLOAD_BYTES`
    422  the request is malformed, or the storage refused it as invalid
    507  the disk is full

The sending device is learned from Tailscale (`Identity`), not from the request. Retries
are safe: sending the same interaction again returns `200 existing` and changes nothing.
"""

import errno
import logging
from collections.abc import Awaitable, Callable, Collection
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import Json
from starlette.exceptions import HTTPException as StarletteHTTPException
from vitality_protocol.messages import ErrorReply, Health, Interaction, UploadReply
from vitality_protocol.routes import HEALTH_PATH, INTERACTIONS_PATH, MAX_UPLOAD_BYTES

from vitality_server.identity import Identity
from vitality_server.storage import ConflictingUpload, InteractionStore, InvalidUpload

log = logging.getLogger(__name__)

_ERROR_STATUSES = (403, 409, 411, 413, 422, 507)


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse(ErrorReply(error=message).model_dump(mode="json"), status_code=status)


def create_app(store: InteractionStore, identity: Identity,
               allowed_clients: Collection[str]) -> FastAPI:
    app = FastAPI(title="Vitality server")

    @app.middleware("http")
    async def limit_upload_size(request: Request,
                                call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        if request.method == "POST":
            length = request.headers.get("content-length")
            if length is None:
                log.warning("rejected %s: no Content-Length", _peer(request))
                return _error(411, "Content-Length required")
            if not length.isdigit() or int(length) > MAX_UPLOAD_BYTES:
                log.warning("rejected %s: request too large (Content-Length %s)", _peer(request), length)
                return _error(413, f"request larger than {MAX_UPLOAD_BYTES} bytes")
        return await call_next(request)

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

    @app.post(INTERACTIONS_PATH, responses={s: {"model": ErrorReply} for s in _ERROR_STATUSES})
    async def post_interaction(request: Request, response: Response,
                               interaction: Annotated[Json[Interaction], Form()],
                               shots: Annotated[list[UploadFile], File()]) -> UploadReply:
        device = await identity.who(request.client.host) if request.client else None
        if device is None or device not in allowed_clients:
            reason = "unknown to Tailscale" if device is None else "not in allowed_clients"
            log.warning("rejected %s: device not allowed (%s)", device or _peer(request), reason)
            raise HTTPException(403, "device not allowed")

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
            if e.errno != errno.ENOSPC:
                raise
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
