"""How far the evidence has come, for Home's header: the newest capture and the newest observation.

Both are read from names alone (folders are listed, no file is opened), so a check is cheap however
much evidence there is. Captures sit in `<captures>/<device>/<day>/<time>/`, observations in
`<model>/observations/<device>/<day>/<time>.md`; days and times are UTC (`2026-11-03`, `14-22-05`).
"""

import os
import re
from datetime import datetime
from pathlib import Path

from vitality_web import schema

_DAY = re.compile(r"\d{4}-\d\d-\d\d")
_TIME = re.compile(r"\d\d-\d\d-\d\d")


def read_pipeline(captures: Path, model: Path, now: datetime | None) -> schema.Pipeline:
    """The clock (`now`, or the server's local time) and the newest capture and observation (UTC), if any."""
    clock = now or datetime.now().astimezone()
    return schema.Pipeline(now=clock.isoformat(timespec="seconds"), last_capture=newest(captures, ""),
                           last_observation=newest(model / "observations", ".md"))


def newest(root: Path, suffix: str) -> str | None:
    """The latest `<device>/<day>/<time><suffix>` under `root`, as a UTC time ("2026-11-03T14:22:05Z")."""
    best: str | None = None
    for device in _names(root):
        for day in sorted((d for d in _names(root / device) if _DAY.fullmatch(d)), reverse=True):
            times = [t.removesuffix(suffix) for t in _names(root / device / day) if t.endswith(suffix)]
            times = [t for t in times if _TIME.fullmatch(t)]
            if times:
                stamp = f"{day}T{max(times).replace('-', ':')}Z"
                best = max(best or stamp, stamp)
                break
    return best


def _names(folder: Path) -> list[str]:
    try:
        return [entry.name for entry in os.scandir(folder)]
    except OSError:
        return []
