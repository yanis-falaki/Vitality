"""The server's HTTP routes, and what each accepts and returns.

`POST /interactions`
    Request: multipart. Part `interaction` = `Interaction` JSON; one part `shots` per
    file, its filename = `Shot.file`.
    Reply: `201` `UploadReply(stored="new")`, `200` `UploadReply(stored="existing")`,
    or an `ErrorReply` with an error status.

`GET /health`
    Reply: `200` `Health`.

The interaction's name appears only in `interaction.json`, not in the URL, so there is one
source of truth. The sending device is not in the request either: the server learns it from
Tailscale.

Retries are safe: sending the same interaction again returns `200 existing` and changes
nothing. Unlike HTTP's usual assumption for `POST`, this route is idempotent, and the
uploader relies on that.
"""

INTERACTIONS_PATH = "/interactions"
HEALTH_PATH = "/health"

INTERACTION_FIELD = "interaction"
SHOTS_FIELD = "shots"

MAX_UPLOAD_BYTES = 50 * 1024 * 1024
