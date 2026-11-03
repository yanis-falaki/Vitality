"""Finding the interactions that still need observing.

An interaction is a folder `<captures_dir>/<device>/<YYYY-MM-DD>/<HH-MM-SS>[-n]` holding an
`interaction.json`; its name is that path relative to `captures_dir`. Observing it writes two
files, `<model_dir>/observations/<name>.md` and `<model_dir>/operations/<name>.md`; it counts
as observed only when both exist and are non-empty.
Interactions the observer gave up on are listed in `skipped.txt`, one `<name>\\t<reason>` per line.
"""

from collections.abc import Container
from dataclasses import dataclass
from pathlib import Path

from vitality_observer.config import ObserverConfig


@dataclass(frozen=True)
class Interaction:
    """One captured interaction. Sort lists of them with `chronological_key(i.name)`, or `sort_key(i.name)` within a device."""

    name: str           # `<device>/<YYYY-MM-DD>/<HH-MM-SS>[-n]`, relative to `captures_dir`
    folder: Path        # the captured folder, holding `interaction.json` and the screenshots
    observation: Path   # where its observation is (or will be) written
    operations: Path    # where its operations are (or will be) written


def device_of(name: str) -> str:
    """The device an interaction name belongs to: its first `/`-separated part."""
    return name.split("/")[0]


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


def chronological_key(name: str) -> tuple[str, str, int, str]:
    """The ordering key for interactions across devices: (day, time, suffix number, device).

    Sorts by when the interaction started, whatever device it came from; the device only
    breaks ties. Raises ValueError unless the name has three `/`-separated parts.
    """
    device, day, time, number = sort_key(name)
    return day, time, number, device


def interactions(config: ObserverConfig) -> list[Interaction]:
    """Every interaction under `captures_dir`, oldest first across devices, by `chronological_key`.

    Only folders exactly three levels under `captures_dir`, holding an `interaction.json`,
    count; a missing `captures_dir` yields an empty list. Whether they are observed is not
    looked at.
    """
    found: list[Interaction] = []
    for folder in config.captures_dir.glob("*/*/*"):
        if not folder.is_dir() or not (folder / "interaction.json").is_file():
            continue
        name = folder.relative_to(config.captures_dir).as_posix()
        found.append(Interaction(
            name,
            folder,
            config.model_dir / "observations" / f"{name}.md",
            config.model_dir / "operations" / f"{name}.md",
        ))
    found.sort(key=lambda interaction: chronological_key(interaction.name))
    return found


def unobserved(config: ObserverConfig, skipped: Container[str]) -> list[Interaction]:
    """The `interactions` not in `skipped` whose observation or operations file is missing or empty, oldest first."""
    return [
        i for i in interactions(config)
        if i.name not in skipped and not (_is_written(i.observation) and _is_written(i.operations))
    ]


def read_skipped(path: Path) -> dict[str, str]:
    """The skipped interactions as `{name: reason}`; a missing file means none."""
    try:
        text = path.read_text(encoding="utf-8")
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
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"{name}\t{reason}\n")


def _is_written(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False
