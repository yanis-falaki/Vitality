"""A small fictional user model, built for the tests and the smoke run; never the owner's data.

`build(root)` writes three folders under `root` and returns them as a `Fixture`:

    model/      a git repo: 2 strivings, 5 activities (and a malformed sixth), 28 actions over three
                days (2026-11-02 to 04, three sittings a day), their observations and operations;
                the last two interactions are untracked (waiting for the modeler)
    state/      the modeler's status.json and sessions.jsonl (with a torn line)
    captures/   placeholder screenshots (generated PNGs) per interaction, and two links that lead
                outside the folder
    secret.png  the file those links point at

Times are local (-05:00); interaction names and action ids are UTC, as in the real model.
"""

import json
import os
import struct
import subprocess
import zlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from vitality_usermodel.formats import (
    ActionMeta,
    ActivityMeta,
    ObservationMeta,
    Operation,
    OperationsMeta,
    Relations,
    StrivingMeta,
    write_file,
)

LOCAL = timezone(timedelta(hours=-5))
DEVICE = "laptop"


@dataclass(frozen=True)
class Fixture:
    model: Path
    state: Path
    captures: Path
    actions: list[str]           # action ids, by start
    interactions: list[str]      # every interaction name, by start (cited and waiting)
    waiting: list[str]           # the untracked interactions


# (day, "HH:MM" local, minutes, activity or None, summary, operations, shares the previous action's last interaction)
_ACTIONS: list[tuple[str, str, int, str | None, str, list[str], bool]] = [
    ("2026-11-02", "09:04", 12, "act-0001", "Set up a new Rust crate for the key-value store",
     ["Ran cargo new kvstore in the terminal", "Opened Cargo.toml and added the serde dependency",
      "Wrote an empty Store struct in lib.rs"], False),
    ("2026-11-02", "09:18", 9, "act-0001", "Read the Rust book's chapter on HashMap",
     ["Opened doc.rust-lang.org on the collections chapter", "Scrolled through the HashMap entry API examples"], False),
    ("2026-11-02", "09:29", 14, "act-0001", "Implemented get and put on the in-memory store",
     ["Wrote put() inserting into a HashMap", "Wrote get() returning an Option",
      "Ran cargo test; two tests passed"], True),
    ("2026-11-02", "09:46", 6, "act-0002", "Wrote a journal note about ownership errors",
     ["Opened notes.md in the editor", "Typed a paragraph about borrowing a value twice"], False),
    ("2026-11-02", "12:32", 8, "act-0003", "Listed meals for the week",
     ["Opened the grocery spreadsheet", "Typed five dinners into the Monday to Friday rows"], False),
    ("2026-11-02", "12:41", 5, "act-0003", "Ordered groceries online",
     ["Added oats, lentils and spinach to a grocery cart", "Chose a Wednesday delivery slot"], False),
    ("2026-11-02", "21:40", 18, "act-0004", "Watched a series of cooking videos",
     ["Opened a video site's home page", "Played a 12-minute video on knife skills",
      "Clicked the next recommended video"], False),
    ("2026-11-02", "22:02", 11, "act-0004", "Kept watching recommended videos past 10 p.m.",
     ["Played a video about city transit maps", "Dismissed the 'take a break' reminder"], False),
    ("2026-11-02", "22:15", 7, "act-0005", "Sorted photos from the weekend",
     ["Opened the photos folder for Saturday", "Moved six blurry photos to a discard folder"], False),
    ("2026-11-03", "08:50", 4, "act-0002", "Reviewed yesterday's journal note",
     ["Opened notes.md", "Read the paragraph about borrowing"], False),
    ("2026-11-03", "08:56", 16, "act-0001", "Added a write-ahead log to the store",
     ["Created wal.rs", "Wrote an append() function that writes length-prefixed records",
      "Ran cargo build and fixed a missing import"], False),
    ("2026-11-03", "09:14", 10, "act-0001", "Read about write-ahead logging in a database book",
     ["Opened a PDF chapter on logging and recovery", "Highlighted the paragraph on fsync"], False),
    ("2026-11-03", "09:26", 13, "act-0001", "Replayed the log on startup",
     ["Wrote a replay() function reading the records back", "Added a test that restarts the store",
      "Ran cargo test; one test failed, then passed after a fix"], False),
    ("2026-11-03", "09:41", 3, None, "Checked the weather", ["Opened a weather page"], False),
    ("2026-11-03", "13:10", 6, "act-0003", "Adjusted the dinner plan for Wednesday",
     ["Swapped lentil soup for a stir-fry in the spreadsheet"], False),
    ("2026-11-03", "13:17", 4, "act-0003", "Checked the grocery delivery time",
     ["Opened the order page", "Read the delivery window of 5 to 7 p.m."], False),
    ("2026-11-03", "20:30", 9, "act-0002", "Wrote up what the log replay taught",
     ["Opened notes.md", "Wrote three bullet points about crash recovery"], False),
    ("2026-11-03", "20:41", 22, "act-0004", "Watched a long documentary on submarines",
     ["Searched the video site for submarine documentaries", "Played a 45-minute documentary",
      "Turned on full screen"], False),
    ("2026-11-03", "21:05", 12, "act-0004", "Watched short clips after the documentary",
     ["Played four short clips from the sidebar", "Liked one clip"], False),
    ("2026-11-03", "21:19", 8, "act-0005", "Backed up photos to an external drive",
     ["Copied the October folder to a USB drive", "Waited for the copy dialog to finish"], False),
    ("2026-11-04", "09:02", 15, "act-0001", "Added compaction to the key-value store",
     ["Wrote compact() rewriting live keys into a new log", "Renamed the new log over the old one",
      "Ran cargo test; all five tests passed"], False),
    ("2026-11-04", "09:19", 8, "act-0001", "Benchmarked put with criterion",
     ["Added criterion to the dev-dependencies", "Ran cargo bench and read the mean time"], False),
    ("2026-11-04", "09:29", 5, "act-0002", "Noted the benchmark result in the journal",
     ["Opened notes.md", "Typed 'put: 1.8 µs mean'"], False),
    ("2026-11-04", "09:36", 11, "act-0001", "Read the documentation of File::sync_all",
     ["Opened the standard library docs for File", "Read the sync_all section", "Copied an example into wal.rs"], False),
    ("2026-11-04", "12:45", 7, "act-0003", "Ticked off the delivery and planned Thursday",
     ["Ticked off delivered items in the spreadsheet", "Typed Thursday's dinner"], False),
    ("2026-11-04", "22:10", 14, "act-0004", "Watched videos instead of going to bed",
     ["Opened the video site", "Played three videos about woodworking"], False),
    ("2026-11-04", "22:26", 6, "act-0005", "Picked favourite photos for an album",
     ["Starred eight photos in the viewer", "Created an album named Autumn"], False),
    ("2026-11-04", "22:34", 3, None, "Set an alarm for the morning",
     ["Opened the clock app", "Set an alarm for 7:00"], False),
]

# Evidence not yet modeled: (day, "HH:MM", minutes, operations).
_WAITING = [
    ("2026-11-04", "22:40", 2, ["Opened the video site again", "Closed the tab after a minute"]),
    ("2026-11-04", "22:44", 1, ["Closed the laptop lid"]),
]

# What each kind of work looks like on screen (the placeholder screenshot drawn for it).
_SCENE = {"act-0001": "editor", "act-0002": "notes", "act-0003": "sheet", "act-0004": "video",
          "act-0005": "photos", None: "browser"}


def build(root: Path) -> Fixture:
    """Write the fixture model, records and captures under `root` (which must not hold them yet)."""
    model, state, captures = root / "model", root / "state", root / "captures"
    for folder in (model, state, captures):
        folder.mkdir(parents=True)
    _git(model, "init", "-q")
    _write_strivings(model)
    _write_activities(model)
    images = {scene: _png(scene) for scene in set(_SCENE.values())}

    actions: list[str] = []
    interactions: list[str] = []
    pending: list[tuple[str, list[Operation], datetime, datetime, str]] = []  # interactions to write
    for day, at, minutes, activity, summary, texts, shares in _ACTIONS:
        start = _local(day, at)
        end = start + timedelta(minutes=minutes)
        refs: list[str] = []
        if shares and pending:
            # The first operation was done in the previous action's last interaction.
            name, ops, i_start, _, scene = pending[-1]
            ops.append(Operation(id=f"op{len(ops) + 1}", text=texts[0], confidence=7))
            pending[-1] = (name, ops, i_start, start + timedelta(minutes=1), scene)
            refs.append(f"{name}#op{len(ops)}")
            texts = texts[1:]
        parts = [texts] if minutes < 10 or len(texts) < 2 else [texts[: len(texts) // 2], texts[len(texts) // 2:]]
        for i, part in enumerate(parts):
            i_start = start + timedelta(minutes=minutes * i / len(parts))
            i_end = start + timedelta(minutes=minutes * (i + 1) / len(parts))
            name = _name(i_start)
            ops = [Operation(id=f"op{n + 1}", text=t, confidence=8 - n % 3) for n, t in enumerate(part)]
            pending.append((name, ops, i_start, i_end, _scene(activity, summary)))
            refs += [f"{name}#{op.id}" for op in ops]
        action_id = _name(start).split("/", 1)[1]
        write_file(model / "actions" / f"{action_id}.md", ActionMeta(
            id=action_id, summary=summary, start=start, end=end, operations=refs,
            part_of=[activity] if activity else [], confidence=6 + len(refs) % 4),
            f"{summary}. These operations follow one another within a few minutes and serve one "
            f"purpose, so they make one action.")
        actions.append(action_id)

    for name, ops, i_start, i_end, scene in pending:
        _write_evidence(model, captures, images[scene], name, ops, i_start, i_end)
        interactions.append(name)
    _write_broken(model)
    (model / ".next-ids").write_text(json.dumps({"activity": 6, "striving": 2}) + "\n", encoding="utf-8")
    _git(model, "add", "-A")
    _git(model, "-c", "user.name=Fixture", "-c", "user.email=fixture@localhost", "commit", "-q", "-m", "model: fixture")

    waiting: list[str] = []
    for day, at, minutes, texts in _WAITING:
        start = _local(day, at)
        name = _name(start)
        ops = [Operation(id=f"op{n + 1}", text=t, confidence=6) for n, t in enumerate(texts)]
        _write_evidence(model, captures, images["video"], name, ops, start, start + timedelta(minutes=minutes))
        interactions.append(name)
        waiting.append(name)

    _write_state(state)
    (root / "secret.png").write_bytes(images["browser"])
    os.symlink("../../secret.png", captures / DEVICE / "escape.png")
    os.symlink(root, captures / DEVICE / "outside")
    return Fixture(model=model, state=state, captures=captures, actions=actions, interactions=interactions,
                   waiting=waiting)


def _write_strivings(model: Path) -> None:
    write_file(model / "strivings" / "str-0001.md", StrivingMeta(
        id="str-0001", title="Become a capable systems programmer", confidence=7,
        needs=["competence", "growth", "understanding"], orientation="approach",
        aspiration_vs_obligation="ideal", autonomy="autonomous",
        relations=Relations(conflicts_with=["str-0002"])),
        "Most mornings begin in a terminal: a toy key-value store in Rust grows a little each day "
        "(2026-11-02/14-04-00, 2026-11-03/13-56-00), and the reading around it is about how real "
        "databases survive crashes. The work is chosen, not assigned: there is no deadline in sight "
        "and no one else is named.\n\n"
        "The **identity link** is plain in the journal (act-0002): what was learned is written down "
        "in the owner's own words. It competes with str-0002 for the late evening.")
    write_file(model / "strivings" / "str-0002.md", StrivingMeta(
        id="str-0002", title="Keep a steady, healthy daily rhythm", confidence=5,
        needs=["safety_security", "order_predictability", "nurturance"], orientation="mixed",
        aspiration_vs_obligation="mixed", autonomy="mixed"),
        "Meals are planned at lunch on most days (act-0003), and a journal is kept. Against it, "
        "videos run past ten at night three days in a row (act-0004), which reads as the rhythm "
        "slipping rather than as a choice.\n\n"
        "Confidence is moderate: three days are little to go on.")


def _write_activities(model: Path) -> None:
    def activity(id: str, title: str, part_of: list[str], hinders: list[str], confidence: int, status: str,
                 first: str, last: str, body: str) -> None:
        write_file(model / "activities" / f"{id}.md", ActivityMeta.model_validate(dict(
            id=id, title=title, part_of=part_of, hinders=hinders, confidence=confidence, status=status,
            first_seen=_local(*first.split()), last_seen=_local(*last.split()))), body)

    activity("act-0001", "Building a toy key-value store in Rust", ["str-0001"], [], 8, "established",
             "2026-11-02 09:04", "2026-11-04 09:47",
             "A small key-value store, built step by step: an in-memory map first "
             "(2026-11-02/14-29-00), then a write-ahead log and its replay (2026-11-03/14-26-00), then "
             "compaction and a benchmark. Each step is read about before it is written, in the Rust "
             "book or a database text.\n\n- Mornings only, before nine-thirty or so\n- Tests run after "
             "every change\n\nIt serves str-0001; the reading belongs here rather than apart.")
    activity("act-0002", "Keeping a learning journal", ["str-0001", "str-0002"], [], 5, "tentative",
             "2026-11-02 09:46", "2026-11-04 09:34",
             "Short notes in `notes.md` after a piece of work: what went wrong with borrowing, what "
             "the log replay taught (2026-11-04/01-30-00), a benchmark number. It may be part of "
             "act-0001 rather than its own activity; kept apart because it is also a habit of the day.")
    activity("act-0003", "Planning the week's meals and groceries", ["str-0002"], [], 7, "established",
             "2026-11-02 12:32", "2026-11-04 12:52",
             "A grocery spreadsheet, opened at lunch: dinners for the week, an order, a change of plan "
             "when something is missing. Done briskly, five to ten minutes at a time.")
    activity("act-0004", "Watching videos late at night", [], ["str-0002"], 4, "tentative",
             "2026-11-02 21:40", "2026-11-04 22:24",
             "Recommended videos after nine, one leading to the next; a 'take a break' reminder "
             "dismissed (2026-11-03/03-02-00). No striving it serves is clear yet; it works against "
             "str-0002.")
    activity("act-0005", "Sorting and backing up photos", [], [], 3, "tentative",
             "2026-11-02 22:15", "2026-11-04 22:32",
             "A few minutes with the photo folders at the end of an evening: discarding, backing up, "
             "making an album. Too little to say what it is for.")


def _write_broken(model: Path) -> None:
    # A malformed file: the API must skip it and list it in `problems`.
    (model / "activities" / "act-0006.md").write_text(
        "---\nid: act-0006\ntitle: Half-written\nconfidence: eleven\n---\n\nBroken on purpose.\n", encoding="utf-8")


def _write_evidence(model: Path, captures: Path, image: bytes, name: str, ops: list[Operation],
                    start: datetime, end: datetime) -> None:
    write_file(model / "operations" / f"{name}.md", OperationsMeta(
        interaction=name, device=DEVICE, start=start, end=end, operations=ops))
    lines = "\n".join(f"- {op.text}" for op in ops)
    extra = ""
    if name.endswith("14-04-00"):
        # Screen text that looks like markup must be shown as text, never run.
        extra = "\n\nA page title read `<script>alert(1)</script>` and <img src=x onerror=alert(2)> appeared."
    write_file(model / "observations" / f"{name}.md", ObservationMeta(
        interaction=name, device=DEVICE, start=start, end=end),
        f"The screen shows one window in front.\n\n{lines}{extra}")
    folder = captures / name
    folder.mkdir(parents=True)
    seconds = int((end - start).total_seconds())
    for n in range(1 + min(2, seconds // 240)):
        (folder / f"{n * seconds // 3:04d}.png").write_bytes(image)
    (folder / "interaction.json").write_text("{}\n", encoding="utf-8")


def _write_state(state: Path) -> None:
    (state / "status.json").write_text(json.dumps({"state": "idle", "since": "2026-11-04T23:20:00-05:00"}) + "\n")
    sessions = [
        {"run": "2026-11-03T04-00-00Z", "started": "2026-11-03T04:00:00Z", "ended": "2026-11-03T04:09:12Z",
         "trigger": "sitting", "offered": ["x"], "operations": {"count": 22}, "outcome": "committed",
         "changes": {"actions": 9, "activities_created": 5, "activities_revised": 0, "activities_merged": 0,
                     "strivings_created": 2, "strivings_revised": 0}, "cost": 1.42},
        {"run": "2026-11-04T03-00-00Z", "started": "2026-11-04T03:00:00Z", "ended": "2026-11-04T03:06:40Z",
         "trigger": "sitting", "operations": {"count": 25}, "outcome": "committed",
         "changes": {"actions": 11, "activities_created": 0, "activities_revised": 3, "activities_merged": 0,
                     "strivings_created": 0, "strivings_revised": 1}, "cost": 1.18},
        {"run": "2026-11-05T04-20-00Z", "started": "2026-11-05T04:20:00Z", "ended": "2026-11-05T04:26:02Z",
         "trigger": "sitting", "operations": {"count": 19}, "outcome": "committed",
         "changes": {"actions": 8, "activities_created": 0, "activities_revised": 4, "activities_merged": 0,
                     "strivings_created": 0, "strivings_revised": 2}, "cost": 0.97},
    ]
    text = "".join(json.dumps(s) + "\n" for s in sessions) + '{"run": "torn'
    (state / "sessions.jsonl").write_text(text, encoding="utf-8")


def _local(day: str, at: str) -> datetime:
    return datetime.fromisoformat(f"{day}T{at}:00").replace(tzinfo=LOCAL)


def _name(start: datetime) -> str:
    return f"{DEVICE}/{start.astimezone(timezone.utc):%Y-%m-%d/%H-%M-%S}"


def _scene(activity: str | None, summary: str) -> str:
    if activity == "act-0001" and summary.startswith("Read"):
        return "browser"
    return _SCENE[activity]


def _git(model: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(model), *args], check=True)


# -- placeholder screenshots: flat shapes that suggest a kind of screen -------------------------

_W, _H = 480, 300


def _png(scene: str) -> bytes:
    pixels = bytearray(_W * _H * 3)

    def rect(x: int, y: int, w: int, h: int, colour: tuple[int, int, int]) -> None:
        for row in range(max(0, y), min(_H, y + h)):
            start = (row * _W + max(0, x)) * 3
            pixels[start:start + max(0, min(_W, x + w) - max(0, x)) * 3] = bytes(colour) * max(0, min(_W, x + w) - max(0, x))

    dark = scene in ("editor", "video")
    rect(0, 0, _W, _H, (30, 32, 38) if dark else (246, 245, 241))
    rect(0, 0, _W, 22, (52, 55, 64) if dark else (226, 223, 216))           # the title bar
    for i, c in enumerate([(235, 105, 90), (240, 190, 80), (110, 190, 110)]):
        rect(10 + i * 14, 7, 8, 8, c)
    if scene == "editor":
        rect(0, 22, 110, _H, (40, 43, 50))
        for i in range(12):
            rect(14, 36 + i * 18, 60 + (i * 23) % 30, 6, (90, 95, 108))
        palette = [(198, 120, 221), (97, 175, 239), (152, 195, 121), (229, 192, 123), (171, 178, 191)]
        for i in range(14):
            indent = 4 * ((i * 7) % 3)
            rect(130 + indent * 4, 38 + i * 17, 40 + (i * 37) % 150, 7, palette[i % 5])
            rect(130 + indent * 4 + 50 + (i * 37) % 150, 38 + i * 17, 30 + (i * 13) % 60, 7, palette[(i + 2) % 5])
    elif scene in ("browser", "notes"):
        rect(60, 30, _W - 120, 18, (255, 255, 255))
        rect(70, 64, 220, 14, (60, 60, 70))
        for i in range(11):
            rect(70, 94 + i * 16, _W - 140 - (i * 31) % 90, 6, (150, 150, 160) if scene == "browser" else (120, 110, 100))
        if scene == "browser":
            rect(70, 280 - 40, 140, 26, (80, 105, 180))
    elif scene == "sheet":
        for row in range(12):
            for col in range(5):
                rect(20 + col * 90, 40 + row * 20, 88, 18, (255, 255, 255) if row else (210, 230, 220))
                if row and (row + col) % 3:
                    rect(26 + col * 90, 46 + row * 20, 30 + (row * col * 7) % 50, 6, (110, 120, 115))
    elif scene == "video":
        rect(20, 36, 300, 170, (12, 12, 14))
        rect(150, 105, 40, 34, (220, 60, 60))
        rect(20, 216, 300, 4, (90, 90, 96))
        rect(20, 216, 120, 4, (220, 60, 60))
        for i in range(4):
            rect(336, 36 + i * 60, 124, 52, (60 + i * 20, 70, 90 + i * 15))
    elif scene == "photos":
        for i in range(12):
            r, c = divmod(i, 4)
            rect(24 + c * 112, 40 + r * 84, 104, 76, (120 + (i * 37) % 120, 140 + (i * 53) % 90, 110 + (i * 29) % 120))
    raw = b"".join(b"\x00" + bytes(pixels[row * _W * 3:(row + 1) * _W * 3]) for row in range(_H))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", _W, _H, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))
