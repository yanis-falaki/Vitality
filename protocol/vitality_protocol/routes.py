"""The server's HTTP routes, and what each accepts and returns.

`POST /interactions`
    Request: multipart. Part `interaction` = `Interaction` JSON; one part `shots` per
    file, its filename = `Shot.file`.
    Reply: `201` `UploadReply(stored="new")`, `200` `UploadReply(stored="existing")`,
    or an `ErrorReply` with an error status.

`GET /health`
    Reply: `200` `Health`.

Status codes of `POST /interactions`, and what the client's uploader does with each (every
error carries an `ErrorReply` with the reason):

    201  stored as new                                  mark uploaded, go on with the next
    200  already stored with identical content          mark uploaded, go on with the next
    403  the device is not on the server's tailnet or   keep pending, state "rejected",
         not in `allowed_clients`                       retry every 5 min
    409  an interaction of this name is stored with     mark failed (never retried), go on
         different content (the original is kept)
    411  `Transfer-Encoding` sent, or no                keep pending, state "offline", retry
         `Content-Length`                               with backoff (5 s doubling to 5 min)
    413  larger than `MAX_UPLOAD_BYTES` (50 MiB), by    mark failed, go on with the next
         `Content-Length` or by the bytes received
    422  malformed, or the checks failed (name, files,  mark failed, go on with the next
         sizes, WebP headers)
    500  an unexpected server error (logged there)      keep pending, "offline", backoff
    507  the server's disk is full                      keep pending, "offline", backoff

Any other status, a timeout or no connection counts as offline too.

The interaction's name appears only in `interaction.json`, not in the URL, so there is one
source of truth. The sending device is not in the request either: the server learns it from
Tailscale.

Retries are safe: sending the same interaction again returns `200 existing` and changes
nothing. Unlike HTTP's usual assumption for `POST`, this route is idempotent, and the
uploader relies on that.
"""


INTERACTIONS_PATH = "/interactions"
"""The path of `POST /interactions`."""

HEALTH_PATH = "/health"
"""The path of `GET /health`."""

INTERACTION_FIELD = "interaction"
"""The multipart part that carries the `Interaction` JSON."""

SHOTS_FIELD = "shots"
"""The multipart part name used once per shot file."""

MAX_UPLOAD_BYTES = 50 * 1024 * 1024
"""The largest request body the server accepts (50 MiB); larger is `413`."""
