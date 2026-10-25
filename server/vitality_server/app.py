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

from collections.abc import Collection

from fastapi import FastAPI

from vitality_server.errors import add_error_handlers
from vitality_server.gate import UploadGate
from vitality_server.identity import Identity
from vitality_server.routes import health, interactions
from vitality_server.storage import InteractionStore


# == Contract =============================================================================

def create_app(store: InteractionStore, identity: Identity,
               allowed_clients: Collection[str]) -> FastAPI:
    """The server's ASGI app, serving the routes above on top of `store`.

    Every request first passes `UploadGate`, which learns the sending device from
    `identity` and lets only `allowed_clients` upload. Builds the app only: opens no
    socket and touches no disk.
    """
    app = FastAPI(title="Vitality server")
    app.state.store = store  # the routes read it from here
    add_error_handlers(app)  # every error leaves as an ErrorReply
    app.include_router(interactions.router)  # POST /interactions
    app.include_router(health.router)  # GET /health
    # the size and device checks, run before the body is parsed
    app.add_middleware(UploadGate, identity=identity, allowed_clients=allowed_clients)
    return app
