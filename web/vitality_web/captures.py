"""The screenshots under the captures folder: listing an interaction's, and finding one safely.

An interaction's screenshots are the image files in `<captures>/<interaction>/` (the server's layout:
`0000.webp`, `0012.webp`, ...); its `interaction.json` says, per shot, which screen it shows when the
client captures several (read leniently: a field that is missing or of another type is left out). Only `.webp`, `.png` and `.jpg` are served, and only from inside the
captures folder: a path with `..` or a link that leads outside it is refused.
"""

import json
from pathlib import Path

from vitality_web import schema

IMAGE_TYPES = {".webp": "image/webp", ".png": "image/png", ".jpg": "image/jpeg"}


def screenshots(root: Path, interaction: str) -> list[str]:
    """The screenshot file names of `interaction`, sorted; none if its folder is missing or outside `root`."""
    folder = resolve(root, interaction, image=False)
    if folder is None or not folder.is_dir():
        return []
    return sorted(p.name for p in folder.iterdir() if p.suffix in IMAGE_TYPES and resolve(root, f"{interaction}/{p.name}"))


def shots(root: Path, interaction: str) -> list[schema.Shot]:
    """The screenshots of `interaction` with their screens, in the order `interaction.json` lists them
    (those it does not list after, by name); plain file names when it is missing or says nothing."""
    files = screenshots(root, interaction)
    listed: dict[str, schema.Shot] = {}
    found = resolve(root, f"{interaction}/interaction.json", image=False)
    try:
        data = json.loads(found.read_text(encoding="utf-8")) if found and found.is_file() else {}
    except (OSError, ValueError):
        data = {}
    raw = data.get("shots") if isinstance(data, dict) else None
    for shot in raw if isinstance(raw, list) else []:
        if not isinstance(shot, dict) or shot.get("file") not in files:
            continue
        screen, position, focused = shot.get("screen"), shot.get("screen_position"), shot.get("focused")
        valid = isinstance(position, list) and len(position) == 2 and all(type(v) is int for v in position)
        listed[shot["file"]] = schema.Shot(
            file=shot["file"], screen=screen if type(screen) is int and screen >= 1 else None,
            screen_position=position if valid else None,
            focused=focused if isinstance(focused, bool) else None)
    return [*listed.values(), *(schema.Shot(file=f) for f in files if f not in listed)]


def resolve(root: Path, relative: str, *, image: bool = True) -> Path | None:
    """The real path of `relative` inside `root`, or None if it leaves `root`, or (with `image`) is not an image file."""
    parts = Path(relative).parts
    if not parts or Path(relative).is_absolute() or ".." in parts:
        return None
    try:
        base = root.resolve(strict=True)
        path = (base / relative).resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    if not path.is_relative_to(base) or path == base:
        return None
    if image and (path.suffix not in IMAGE_TYPES or not path.is_file()):
        return None
    return path
