"""Every error reply is an `ErrorReply`: the helper that builds one, and the handlers that
turn the exceptions FastAPI raises into one."""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from vitality_protocol.messages import ErrorReply


# == Contract =============================================================================

# The statuses a route can answer with an `ErrorReply`, for the OpenAPI `responses` of a route.
ERROR_STATUSES = (403, 409, 411, 413, 422, 507)


def error(status: int, message: str) -> JSONResponse:
    """An `ErrorReply` carrying `message`, with the given status."""
    return JSONResponse(ErrorReply(error=message).model_dump(mode="json"), status_code=status)


def add_error_handlers(app: FastAPI) -> None:
    """Make 422 (malformed request), any raised HTTP error and anything unexpected (500)
    reply with an `ErrorReply`.

    A 422 carries the first problem found, as `<field>: <message>`; a raised HTTP error
    keeps its status and detail; an unexpected exception is logged with its traceback and
    answers 500 "internal server error", never the exception's text.
    """

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0]
        field = ".".join(str(part) for part in first["loc"][1:])
        message = f"{field}: {first['msg']}" if field else first["msg"]
        log.warning("rejected %s: %s", _peer(request), message)
        return error(422, message)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return error(exc.status_code, str(exc.detail))

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        log.error("failed %s %s", request.method, request.url.path, exc_info=exc)
        return error(500, "internal server error")


# == Implementation =======================================================================

log = logging.getLogger(__name__)


def _peer(request: Request) -> str:
    return request.client.host if request.client else "unknown client"
