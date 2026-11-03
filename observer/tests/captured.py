"""Builders of captured interactions on disk, for the observer's tests."""

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from vitality_protocol.messages import Interaction as Captured
from vitality_protocol.messages import Shot

from vitality_observer.work import Interaction


def make_captured(start: str, tz: str = "America/Toronto", seconds: tuple[int, ...] = (0, 7)) -> Captured:
    first = datetime.fromisoformat(start)
    shots = [
        Shot(
            file=f"{s:04d}.webp",
            ts=first.replace(second=first.second + s),
            trigger="activity",
            width=10,
            height=10,
            bytes=1,
        )
        for s in seconds
    ]
    return Captured(
        name="2026-11-03/14-22-05",
        source="screen",
        start=first,
        end=shots[-1].ts,
        local_start=first.astimezone(ZoneInfo(tz)),
        tz=tz,
        shots=shots,
    )


def make_interaction(tmp_path: Path, captured: Captured, *, write_shots: bool = True) -> Interaction:
    folder = tmp_path / "captures" / "laptop" / captured.name
    folder.mkdir(parents=True)
    (folder / "interaction.json").write_text(captured.model_dump_json(), encoding="utf-8")
    if write_shots:
        for shot in captured.shots:
            (folder / shot.file).write_bytes(b"fake-" + shot.file.encode())
    return Interaction(
        f"laptop/{captured.name}",
        folder,
        tmp_path / "model" / "observations" / f"laptop/{captured.name}.md",
        tmp_path / "model" / "operations" / f"laptop/{captured.name}.md",
    )
