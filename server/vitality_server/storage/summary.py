"""A read-only look at the index: what each device has uploaded.

A plain function module for the command line (`vitality-server status`), which must work
whether or not the server is running and must never change the data folder.
"""

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote


@dataclass(frozen=True)
class DeviceSummary:
    """What one device (client) has uploaded."""

    client: str
    interactions: int
    shots: int
    bytes: int                      # the sum of the shots' sizes, as recorded in the index
    last_received: str | None       # the newest `received_at` (UTC ISO 8601), None if there is none


def read_summary(data_dir: Path) -> list[DeviceSummary]:
    """One `DeviceSummary` per client in `data_dir/index.sqlite`, sorted by client name.

    Reads the index only (no walk of captures/) and never creates or changes a file. While
    the server has the index open, its `-wal` file exists and the index is opened read-only
    (`mode=ro`) alongside it, so recent uploads are included. Otherwise nothing is writing,
    and a read-only open would still leave SQLite's `-shm` file behind in a writable folder
    and fail in a read-only one, so it opens with `immutable=1` instead. A read-only open
    that SQLite refuses (for example the server's files belong to another user) is retried
    that way too, which can miss uploads the server has not yet checkpointed.

    Raises FileNotFoundError if there is no index, and `sqlite3.Error` if it cannot be read
    or is not an index.
    """
    index = Path(data_dir) / "index.sqlite"
    if not index.is_file():
        raise FileNotFoundError(2, "No such file or directory", str(index))
    if not Path(f"{index}-wal").exists():
        return _read(_uri(index, "mode=ro&immutable=1"))
    try:
        return _read(_uri(index, "mode=ro"))
    except sqlite3.OperationalError:
        return _read(_uri(index, "mode=ro&immutable=1"))


_INTERACTIONS = "SELECT client, COUNT(*), MAX(received_at) FROM interactions GROUP BY client"
_SHOTS = "SELECT client, COUNT(*), SUM(bytes) FROM shots GROUP BY client"


def _uri(index: Path, options: str) -> str:
    return f"file:{quote(str(index))}?{options}"


def _read(uri: str) -> list[DeviceSummary]:
    db = sqlite3.connect(uri, uri=True)
    try:
        interactions = {client: (count, last) for client, count, last in db.execute(_INTERACTIONS)}
        shots = {client: (count, size) for client, count, size in db.execute(_SHOTS)}
    finally:
        db.close()
    return [DeviceSummary(client, count, *shots.get(client, (0, 0)), last_received=last)
            for client, (count, last) in sorted(interactions.items())]
