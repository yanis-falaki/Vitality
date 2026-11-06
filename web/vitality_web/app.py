"""The web app's HTTP side: the read-only API, the screenshots, and the built app (web spec, section 4).

`create_app(model, state, captures, app_dir)` builds the FastAPI application:

    GET /api/model                     the model (`schema.Model`): strivings, activities, actions without bodies
    GET /api/actions/{id}              one action with its operations and body (`schema.ActionDetail`), or 404
    GET /api/interactions?from=&to=    summaries of the cited interactions started on those days (`schema.Interactions`)
    GET /api/evidence/{interaction}    one interaction's evidence (`schema.Evidence`), or 404
    GET /api/modeler                   the modeler's state (`schema.Modeler`)
    GET /api/health                    {"ok": true}, for the container's healthcheck
    GET /captures/{path}               a screenshot, cached for a year, or 404
    anything else                      a file of the built app, else its index.html (client routes)

The model and the interactions are sent gzipped when the browser accepts it, with an ETag, so the
app's once-a-minute refetch of an unchanged model is a 304 with no body. On start, the model is read
once in the background, so the first visit after a restart does not wait for every file to parse.
"""

import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, Response

from vitality_web import captures, schema
from vitality_web.model import Json, ModelReader
from vitality_web.modeler import read_modeler

_YEAR = "public, max-age=31536000, immutable"
_NO_APP = "<!doctype html><title>Vitality</title><p>The app is not built (see web/README.md).</p>"


def create_app(model: Path, state: Path, captures_dir: Path, app_dir: Path | None, fresh: float = 2.0) -> FastAPI:
    """The application over the model folder, the modeler's records, the captures and the built app (if any).

    `fresh`: how many seconds a check of the model's files stands for (see `ModelReader`).
    """
    reader = ModelReader(model, captures_dir, fresh)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        threading.Thread(target=reader.snapshot, name="warm-model", daemon=True).start()
        yield

    app = FastAPI(title="Vitality web", version="1", docs_url=None, redoc_url=None, lifespan=lifespan)

    @app.get("/api/model", response_model=schema.Model)
    def get_model(request: Request) -> Response:
        return _reply(request, reader.snapshot().json)

    @app.get("/api/actions/{id:path}", response_model=schema.ActionDetail,
             responses={404: {"description": "No such action"}})
    def get_action(id: str) -> schema.ActionDetail:
        action = reader.read_action(id)
        if action is None:
            raise HTTPException(404, "no such action")
        return action

    @app.get("/api/interactions", response_model=schema.Interactions)
    def get_interactions(request: Request, first: date | None = Query(None, alias="from"),
                         last: date | None = Query(None, alias="to")) -> Response:
        return _reply(request, Json.of(reader.interactions(first, last)))

    @app.get("/api/evidence/{interaction:path}", response_model=schema.Evidence,
             responses={404: {"description": "No such interaction"}})
    def get_evidence(interaction: str) -> schema.Evidence:
        evidence = reader.read_evidence(interaction)
        if evidence is None:
            raise HTTPException(404, "no such interaction")
        return evidence

    @app.get("/api/modeler", response_model=schema.Modeler)
    def get_modeler() -> schema.Modeler:
        return read_modeler(state, model, reader.operation_counts)

    @app.get("/api/health")
    def get_health() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/api/{rest:path}", include_in_schema=False)
    def api_unknown(rest: str) -> None:
        raise HTTPException(404, "no such endpoint")

    @app.get("/captures/{path:path}", include_in_schema=False)
    def get_capture(path: str) -> FileResponse:
        found = captures.resolve(captures_dir, path)
        if found is None:
            raise HTTPException(404, "no such screenshot")
        return FileResponse(found, media_type=captures.IMAGE_TYPES[found.suffix], headers={"Cache-Control": _YEAR})

    @app.get("/{path:path}", include_in_schema=False, response_model=None)
    def get_app(path: str) -> FileResponse | HTMLResponse:
        if app_dir is None or not (app_dir / "index.html").is_file():
            return HTMLResponse(_NO_APP)
        file = _inside(app_dir, path)
        if file is not None and file.is_file():
            # Vite names its built assets by content, so they never change; index.html always may.
            cache = _YEAR if file.parent.name == "assets" else "no-cache"
            return FileResponse(file, headers={"Cache-Control": cache})
        return FileResponse(app_dir / "index.html", headers={"Cache-Control": "no-cache"})

    return app


def _reply(request: Request, body: Json) -> Response:
    # A JSON body with its ETag: 304 when the browser has it, gzipped when it accepts that.
    headers = {"ETag": body.etag, "Cache-Control": "no-cache", "Vary": "Accept-Encoding"}
    if body.etag in request.headers.get("if-none-match", ""):
        return Response(status_code=304, headers=headers)
    if "gzip" in request.headers.get("accept-encoding", "") and len(body.plain) > 1024:
        return Response(body.gzipped, media_type="application/json", headers={**headers, "Content-Encoding": "gzip"})
    return Response(body.plain, media_type="application/json", headers=headers)


def _inside(root: Path, relative: str) -> Path | None:
    # `relative` under `root`, or None if it would leave it.
    if not relative or ".." in Path(relative).parts:
        return None
    path = (root / relative).resolve()
    return path if path.is_relative_to(root.resolve()) else None
