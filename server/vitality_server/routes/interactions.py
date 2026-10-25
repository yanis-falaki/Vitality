"""`POST /interactions`: store an uploaded interaction and its screenshots.

By the time a request gets here `UploadGate` has checked the size headers and the sending
device. Retries are safe: the store answers `existing` for the same interaction sent again.
"""

import errno
import logging
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import Json
from vitality_protocol.messages import ErrorReply, Interaction, UploadReply
from vitality_protocol.routes import INTERACTIONS_PATH

from vitality_server.errors import ERROR_STATUSES
from vitality_server.storage import ConflictingUpload, InvalidUpload

log = logging.getLogger(__name__)

router = APIRouter()


@router.post(INTERACTIONS_PATH, responses={s: {"model": ErrorReply} for s in ERROR_STATUSES})
async def post_interaction(request: Request, response: Response,
                           interaction: Annotated[Json[Interaction], Form()],
                           shots: Annotated[list[UploadFile], File()]) -> UploadReply:
    device: str = request.state.device  # set by UploadGate, which already checked it
    files: dict[str, bytes] = {}
    for shot in shots:
        if not shot.filename or shot.filename in files:
            log.warning("rejected %s: shot parts need distinct file names", device)
            raise HTTPException(422, "each shot needs its own file name")
        files[shot.filename] = await shot.read()

    try:
        stored = await run_in_threadpool(request.app.state.store.put, device, interaction, files)
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
