"""The playbook appended to every session's system prompt, and its version.

The text is `playbook.md`, shipped inside the package. Its version, recorded with every session, is
the first 12 hex digits of the SHA-256 of its UTF-8 bytes, so any edit gives a new version.
"""

import hashlib
from importlib.resources import files


def version(text: str) -> str:
    """The version of a playbook text: the first 12 hex digits of its SHA-256."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


PLAYBOOK: str = files("vitality_modeler").joinpath("playbook.md").read_text(encoding="utf-8")
PLAYBOOK_VERSION: str = version(PLAYBOOK)
