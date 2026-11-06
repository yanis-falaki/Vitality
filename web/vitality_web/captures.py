"""The screenshots under the captures folder: listing an interaction's, and finding one safely.

An interaction's screenshots are the image files in `<captures>/<interaction>/` (the server's layout:
`0000.webp`, `0012.webp`, ...). Only `.webp`, `.png` and `.jpg` are served, and only from inside the
captures folder: a path with `..` or a link that leads outside it is refused.
"""

from pathlib import Path

IMAGE_TYPES = {".webp": "image/webp", ".png": "image/png", ".jpg": "image/jpeg"}


def screenshots(root: Path, interaction: str) -> list[str]:
    """The screenshot file names of `interaction`, sorted; none if its folder is missing or outside `root`."""
    folder = resolve(root, interaction, image=False)
    if folder is None or not folder.is_dir():
        return []
    return sorted(p.name for p in folder.iterdir() if p.suffix in IMAGE_TYPES and resolve(root, f"{interaction}/{p.name}"))


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
