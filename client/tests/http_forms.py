"""Test helper: read the parts of a multipart request the uploader sent."""

from email.parser import BytesParser
from email.policy import HTTP

import httpx


def form_parts(request: httpx.Request) -> list[tuple[str, str | None, bytes]]:
    """Every part of a multipart request as (field name, file name, content)."""
    head = b"Content-Type: " + request.headers["content-type"].encode() + b"\r\n\r\n"
    message = BytesParser(policy=HTTP).parsebytes(head + request.content)
    return [
        (str(part.get_param("name", header="content-disposition")),
         part.get_filename(),
         bytes(part.get_payload(decode=True)))
        for part in message.iter_parts()
    ]


def form_field(request: httpx.Request, name: str) -> str:
    """The text of the one non-file part called `name`."""
    (content,) = [c for n, f, c in form_parts(request) if n == name and f is None]
    return content.decode()
