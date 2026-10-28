"""Finding the interactions that still need an observation, and the context to give with them.

An interaction is a folder `<captures_dir>/<device>/<YYYY-MM-DD>/<HH-MM-SS>[-n]` holding an
`interaction.json`; its name is that path relative to `captures_dir`. Its observation is
`<model_dir>/observations/<name>.md`, and counts as existing only if the file is non-empty.
Interactions the observer gave up on are listed in `skipped.txt`, one `<name>\\t<reason>` per line.
"""

from collections.abc import Container
from dataclasses import dataclass
from pathlib import Path

from vitality_observer.config import ObserverConfig


@dataclass(frozen=True)
class Interaction:
    """One captured interaction. Sort lists of them with `sort_key(i.name)`."""

    name: str           # `<device>/<YYYY-MM-DD>/<HH-MM-SS>[-n]`, relative to `captures_dir`
    folder: Path        # the captured folder, holding `interaction.json` and the screenshots
    observation: Path   # where its observation is (or will be) written


def sort_key(name: str) -> tuple[str, str, str, int]:
    """The ordering key of an interaction name: (device, day, time, suffix number).

    A name without a clash suffix has suffix number 1, so `17-00-00` sorts before
    `17-00-00-2`, and `-10` after `-9`. Raises ValueError unless the name has three
    `/`-separated parts.
    """
    device, day, last = name.split("/")
    *time_parts, tail = last.split("-")
    if len(time_parts) >= 3 and tail.isdecimal():
        return device, day, "-".join(time_parts), int(tail)
    return device, day, last, 1


def unobserved(config: ObserverConfig, skipped: Container[str]) -> list[Interaction]:
    """Interactions with an `interaction.json`, no non-empty observation, and not in `skipped`.

    Oldest first, by `sort_key`. Only folders exactly three levels under `captures_dir` count;
    a missing `captures_dir` yields an empty list.
    """
    found: list[Interaction] = []
    for folder in config.captures_dir.glob("*/*/*"):
        if not folder.is_dir() or not (folder / "interaction.json").is_file():
            continue
        name = folder.relative_to(config.captures_dir).as_posix()
        if name in skipped:
            continue
        observation = _observation_path(config, name)
        if _is_observed(observation):
            continue
        found.append(Interaction(name, folder, observation))
    found.sort(key=lambda interaction: sort_key(interaction.name))
    return found


def context(config: ObserverConfig, before: Interaction) -> list[tuple[str, str]]:
    """The last `context_size` existing observations of `before`'s device that sort before it.

    Returns `(name, observation text)` pairs, oldest first; observations of earlier days
    count. Only the files returned are read, and never those of another device.
    """
    device = before.name.split("/")[0]
    limit = sort_key(before.name)
    observations = config.model_dir / "observations"
    earlier = [
        name
        for path in (observations / device).glob("*/*.md")
        if _is_observed(path)
        and sort_key(name := path.relative_to(observations).with_suffix("").as_posix()) < limit
    ]
    earlier.sort(key=sort_key)
    return [(name, _observation_path(config, name).read_text()) for name in earlier[-config.context_size:]]


def read_skipped(path: Path) -> dict[str, str]:
    """The skipped interactions as `{name: reason}`; a missing file means none."""
    try:
        text = path.read_text()
    except FileNotFoundError:
        return {}
    skipped: dict[str, str] = {}
    for line in text.splitlines():
        name, _, reason = line.partition("\t")
        if name:
            skipped[name] = reason
    return skipped


def add_skipped(path: Path, name: str, reason: str) -> None:
    """Append `name` to the skipped list, creating the file and its directory if needed.

    Tabs and line breaks in `reason` become spaces, so each entry stays on one line.
    """
    reason = " ".join(reason.replace("\t", " ").splitlines())
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(f"{name}\t{reason}\n")


def _observation_path(config: ObserverConfig, name: str) -> Path:
    return config.model_dir / "observations" / f"{name}.md"


def _is_observed(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False
