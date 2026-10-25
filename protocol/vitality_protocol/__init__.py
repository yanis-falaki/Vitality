"""What the Vitality client and server agree on. Definitions and pure functions only:
no network, no disk.

layout.py     where an interaction's files live and what they are called
messages.py   the typed data that crosses the network (`Interaction`, replies)
routes.py     the server's HTTP routes, with the table of what each accepts and returns
"""

from vitality_protocol.layout import (
    INTERACTION_FILE,
    INTERACTION_NAME_RE,
    SHOT_FILE_RE,
    interaction_name,
    name_matches_start,
    shot_filename,
)
from vitality_protocol.messages import ErrorReply, Health, Interaction, Shot, UploadReply
from vitality_protocol.routes import (
    HEALTH_PATH,
    INTERACTION_FIELD,
    INTERACTIONS_PATH,
    MAX_UPLOAD_BYTES,
    SHOTS_FIELD,
)

__all__ = [
    "ErrorReply",
    "HEALTH_PATH",
    "Health",
    "INTERACTIONS_PATH",
    "INTERACTION_FIELD",
    "INTERACTION_FILE",
    "INTERACTION_NAME_RE",
    "Interaction",
    "MAX_UPLOAD_BYTES",
    "SHOTS_FIELD",
    "SHOT_FILE_RE",
    "Shot",
    "UploadReply",
    "interaction_name",
    "name_matches_start",
    "shot_filename",
]
