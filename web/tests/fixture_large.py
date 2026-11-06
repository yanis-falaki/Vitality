"""A large fictional user model, about four months of it, for trying the app at scale; never the owner's data.

`build_large(root, days=120)` writes the same three folders as `fixture.build` and returns a `Fixture`:

    model/      a git repo: 6 strivings, 25 activities (some gone quiet for over a month, some begun
                late), ~20 actions on most weekdays and a few weekend days (a holiday and some single
                days missed), 2 to 12 interactions per action with 1 to 4 operations each, their
                observations; the last two interactions are untracked (waiting for the modeler)
    state/      the modeler's records, as in the small fixture
    captures/   one to three placeholder screenshots per interaction: hard links to a handful of
                generated images, so the folder costs almost no space

The last day is 2026-11-04, as in the small fixture. Everything is drawn from a seeded random
generator, so the same call writes the same model. Files are written directly (rendered with
`vitality_usermodel.formats.render`), not atomically: the folder is new and nobody reads it yet.
"""

import json
import os
import random
import subprocess
from datetime import date, datetime, timedelta
from pathlib import Path

from pydantic import BaseModel

from fixture import DEVICE, LOCAL, Fixture, _name, _png, _write_state
from vitality_usermodel.formats import (
    ActionMeta,
    ActivityMeta,
    ObservationMeta,
    Operation,
    OperationsMeta,
    Relations,
    StrivingMeta,
    render,
)

LAST_DAY = date(2026, 11, 4)

_STRIVINGS: list[tuple[str, list[str], str]] = [
    ("Become a capable systems programmer", ["competence", "growth", "understanding"], "approach"),
    ("Keep a steady, healthy daily rhythm", ["safety_security", "order_predictability", "nurturance"], "mixed"),
    ("Stay close to family and old friends", ["relatedness", "nurturance"], "approach"),
    ("Earn a living from freelance work", ["safety_security", "status_recognition"], "mixed"),
    ("Make music again", ["stimulation", "self_coherence", "purpose_meaning"], "approach"),
    ("Keep the household running", ["order_predictability", "safety_security"], "avoidance"),
]

# title, strivings served, strivings hindered, first day, last day (offsets from the first day; negative
# from the last), weight, the placeholder screen, what its actions look like
_ACTIVITIES: list[tuple[str, list[int], list[int], int, int, int, str, list[str]]] = [
    ("Building a toy key-value store in Rust", [0], [], 0, -1, 9, "editor",
     ["Added {x} to the key-value store", "Debugged a failing test in {x}", "Refactored {x}"]),
    ("Reading about database internals", [0], [], 0, -1, 4, "browser",
     ["Read a chapter about {x}", "Took notes on {x}", "Compared two papers on {x}"]),
    ("Keeping a learning journal", [0, 1], [], 0, -1, 3, "notes",
     ["Wrote a journal note about {x}", "Reviewed last week's notes on {x}"]),
    ("Solving programming puzzles", [0], [], 0, 52, 3, "editor",
     ["Solved a puzzle about {x}", "Gave up on a puzzle about {x} and read a solution"]),
    ("Contributing to an open-source parser", [0], [], 40, -1, 4, "editor",
     ["Opened a pull request for {x}", "Answered a review comment about {x}", "Read the parser's code for {x}"]),
    ("Planning the week's meals and groceries", [1], [], 0, -1, 4, "sheet",
     ["Planned dinners around {x}", "Ordered groceries including {x}"]),
    ("Watching videos late at night", [], [1], 0, -1, 4, "video",
     ["Watched videos about {x} past ten", "Kept watching recommended videos about {x}"]),
    ("Tracking sleep and exercise", [1], [], 0, 61, 2, "sheet",
     ["Logged a run and {x}", "Looked at last week's sleep chart and {x}"]),
    ("Morning stretching routine", [1], [], 70, -1, 2, "video",
     ["Followed a stretching video on {x}", "Did a ten-minute routine for {x}"]),
    ("Video calls with family", [2], [], 0, -1, 2, "video",
     ["Called home and talked about {x}", "Planned a visit around {x}"]),
    ("Writing long letters to an old friend", [2], [], 0, 45, 2, "notes",
     ["Wrote a letter about {x}", "Read an old letter about {x}"]),
    ("Organising a reunion trip", [2], [5], 55, 95, 3, "browser",
     ["Compared train times for {x}", "Messaged the group about {x}"]),
    ("Freelance web project for a bakery", [3], [], 0, 80, 7, "editor",
     ["Built the bakery site's {x}", "Fixed a layout bug in {x}", "Sent the client a preview of {x}"]),
    ("Freelance data dashboard", [3], [], 60, -1, 6, "sheet",
     ["Built a chart of {x} for the client", "Cleaned the client's data on {x}"]),
    ("Invoicing and taxes", [3, 5], [], 0, -1, 2, "sheet",
     ["Sent an invoice for {x}", "Filed receipts for {x}"]),
    ("Looking for new clients", [3], [], 30, -1, 2, "browser",
     ["Answered a job post about {x}", "Updated the portfolio with {x}"]),
    ("Practising guitar", [4], [], 0, -1, 3, "video",
     ["Practised {x} on the guitar", "Learned the chords of {x}"]),
    ("Recording demos at home", [4], [], 20, 70, 2, "editor",
     ["Recorded a demo of {x}", "Mixed the drums of {x}"]),
    ("Reading about music theory", [4], [], 0, 35, 2, "browser",
     ["Read about {x}", "Watched a lesson on {x}"]),
    ("Paying bills and banking", [5], [], 0, -1, 2, "sheet",
     ["Paid the bill for {x}", "Checked the account for {x}"]),
    ("Fixing things around the flat", [5], [], 0, -1, 1, "browser",
     ["Looked up how to fix {x}", "Ordered a part for {x}"]),
    ("Sorting and backing up photos", [], [], 0, 40, 1, "photos",
     ["Sorted photos of {x}", "Backed up the photos of {x}"]),
    ("Browsing news sites", [], [], 0, -1, 3, "browser",
     ["Read the news about {x}", "Scrolled a news site's front page about {x}"]),
    ("Online chess", [], [1], 0, 50, 2, "browser",
     ["Played three blitz games with {x}", "Analysed a lost game with {x}"]),
    ("Learning to cook new dishes", [1, 2], [], 85, -1, 2, "video",
     ["Watched a recipe for {x}", "Wrote down a recipe for {x}"]),
]

_THINGS = ["write-ahead logs", "compaction", "B-trees", "an LRU cache", "the October invoice", "lentil soup",
           "the train to Lyon", "a jazz standard", "the login page", "a leaking tap", "the summer photos",
           "a sleep schedule", "error handling", "the client's sales figures", "a birthday", "a slow query",
           "the parser's lexer", "hash maps", "the menu page", "a new string set", "the heating bill",
           "an old song", "a weekend run", "the reunion dinner"]

_OPERATIONS = ["Opened a new tab", "Typed a few lines", "Scrolled down the page", "Switched to the terminal",
               "Clicked a link in the sidebar", "Copied a paragraph", "Saved the file", "Read the page for a minute",
               "Searched for {x}", "Opened a document about {x}", "Closed two tabs", "Typed a message about {x}",
               "Ran a command", "Zoomed in on a chart", "Played a video", "Paused the video"]

_SCENES = ["editor", "browser", "notes", "sheet", "video", "photos"]


def build_large(root: Path, days: int = 120, seed: int = 7) -> Fixture:
    """Write the large fixture under `root` (which must not hold it yet); `days` calendar days ending on LAST_DAY."""
    rng = random.Random(seed)
    model, state, captures = root / "model", root / "state", root / "captures"
    for folder in (model, state, captures):
        folder.mkdir(parents=True)
    _git(model, "init", "-q")
    first_day = LAST_DAY - timedelta(days=days - 1)
    images = _images(root)

    calendar = [first_day + timedelta(days=i) for i in range(days)]
    holiday = set(calendar[int(days * 0.35):int(days * 0.35) + 9])  # a holiday of nine days
    active = [d for d in calendar if d not in holiday and rng.random() < (0.93 if d.weekday() < 5 else 0.3)]
    if active[-1] != LAST_DAY:
        active.append(LAST_DAY)

    spans = [(start, end if end >= 0 else days + end) for _, _, _, start, end, _, _, _ in _ACTIVITIES]
    seen: dict[int, list[datetime]] = {}
    actions: list[str] = []
    interactions: list[str] = []
    for d in active:
        offset = (d - first_day).days
        open_ = [i for i, (s, e) in enumerate(spans) if s <= offset <= e]
        weights = [_ACTIVITIES[i][5] for i in open_]
        at = datetime.combine(d, datetime.min.time(), LOCAL)
        for start, end in _sittings(rng, d):
            at = max(at, start)
            while at < end:
                minutes = rng.randint(3, 24)
                activity = None if rng.random() < 0.05 else rng.choices(open_, weights)[0]
                a_end = at + timedelta(minutes=minutes)
                refs = _write_action(model, captures, images, rng, activity, at, a_end, interactions)
                action_id = _name(at).split("/", 1)[1]
                summary = _summary(rng, activity)
                _write(model / "actions" / f"{action_id}.md", ActionMeta(
                    id=action_id, summary=summary, start=at, end=a_end, operations=refs,
                    part_of=[f"act-{activity + 1:04d}"] if activity is not None else [],
                    confidence=rng.randint(4, 9)),
                    f"{summary}. These operations follow one another and serve one purpose, so they make "
                    f"one action ({_name(at).split('/', 1)[1]}).")
                actions.append(action_id)
                if activity is not None:
                    seen.setdefault(activity, []).append(at)
                at = a_end + timedelta(minutes=rng.choice([0, 1, 2, 3, 5, 8, 12]))

    _write_strivings(model)
    _write_activities(model, seen)
    (model / ".next-ids").write_text(json.dumps({"activity": len(_ACTIVITIES) + 1, "striving": len(_STRIVINGS) + 1}) + "\n",
                                     encoding="utf-8")
    _git(model, "add", "-A")
    _git(model, "-c", "user.name=Fixture", "-c", "user.email=fixture@localhost", "commit", "-q", "-m", "model: large fixture")

    waiting: list[str] = []
    last = datetime.combine(LAST_DAY, datetime.min.time(), LOCAL) + timedelta(hours=23, minutes=30)
    for n in range(2):
        start = last + timedelta(minutes=3 * n)
        name = _name(start)
        _write_evidence(model, captures, images, name, [Operation(id="op1", text="Closed the laptop lid", confidence=6)],
                        start, start + timedelta(minutes=1), 1)
        interactions.append(name)
        waiting.append(name)
    _write_state(state)
    return Fixture(model=model, state=state, captures=captures, actions=actions, interactions=interactions,
                   waiting=waiting)


def _sittings(rng: random.Random, d: date) -> list[tuple[datetime, datetime]]:
    # Three or four stretches of the day, about 20 actions in all.
    def at(h: float) -> datetime:
        return datetime.combine(d, datetime.min.time(), LOCAL) + timedelta(minutes=round(h * 60))
    plan = [(8.5, 2.2), (12.4, 0.5), (14.5, 1.6), (20.5, 1.4)]
    if rng.random() < 0.35:
        plan.pop(rng.randrange(len(plan)))
    return [(at(h + rng.uniform(-0.4, 0.4)), at(h + rng.uniform(-0.4, 0.4) + length * rng.uniform(0.8, 1.3)))
            for h, length in plan]


def _summary(rng: random.Random, activity: int | None) -> str:
    if activity is None:
        return rng.choice(["Checked the weather", "Set an alarm", "Looked at the calendar", "Emptied the downloads folder"])
    return rng.choice(_ACTIVITIES[activity][7]).format(x=rng.choice(_THINGS))


def _write_action(model: Path, captures: Path, images: dict[str, list[Path]], rng: random.Random,
                  activity: int | None, start: datetime, end: datetime, interactions: list[str]) -> list[str]:
    # The action's interactions, evenly over its time; returns the operation references.
    count = rng.choices(range(2, 13), [4, 8, 10, 12, 12, 11, 10, 9, 8, 6, 5])[0]
    step = (end - start) / count
    scene = _ACTIVITIES[activity][6] if activity is not None else "browser"
    refs: list[str] = []
    for n in range(count):
        i_start = start + step * n
        i_start -= timedelta(microseconds=i_start.microsecond)
        name = _name(i_start)
        ops = [Operation(id=f"op{k + 1}", text=rng.choice(_OPERATIONS).format(x=rng.choice(_THINGS)),
                         confidence=rng.randint(5, 9)) for k in range(rng.randint(1, 4))]
        shots = 1 + min(2, int(step.total_seconds()) // 120)
        _write_evidence(model, captures, images, name, ops, i_start, start + step * (n + 1), shots, scene)
        interactions.append(name)
        refs += [f"{name}#{op.id}" for op in ops]
    return refs


def _write_evidence(model: Path, captures: Path, images: dict[str, list[Path]], name: str, ops: list[Operation],
                    start: datetime, end: datetime, shots: int, scene: str = "video") -> None:
    _write(model / "operations" / f"{name}.md", OperationsMeta(
        interaction=name, device=DEVICE, start=start, end=end, operations=ops))
    _write(model / "observations" / f"{name}.md", ObservationMeta(interaction=name, device=DEVICE, start=start, end=end),
           "The screen shows one window in front.\n\n" + "\n".join(f"- {op.text}" for op in ops))
    folder = captures / name
    folder.mkdir(parents=True)
    seconds = max(1, int((end - start).total_seconds()))
    for n in range(shots):
        os.link(images[scene][n % len(images[scene])], folder / f"{n * seconds // 3:04d}.png")


def _write_strivings(model: Path) -> None:
    for n, (title, needs, orientation) in enumerate(_STRIVINGS):
        id = f"str-{n + 1:04d}"
        conflicts = ["str-0002"] if n in (0, 4) else []
        _write(model / "strivings" / f"{id}.md", StrivingMeta.model_validate(dict(
            id=id, title=title, confidence=4 + n % 5, needs=needs, orientation=orientation,
            aspiration_vs_obligation=["ideal", "mixed", "ought"][n % 3], autonomy=["autonomous", "mixed"][n % 2],
            relations=Relations(conflicts_with=conflicts))),
            f"{title}, as the model reads it after four months. Most of the evidence comes from the activities "
            f"serving it; see act-{n * 4 + 1:04d} for the steadiest of them.\n\nConfidence grows with every week "
            f"in which it shows up again.")


def _write_activities(model: Path, seen: dict[int, list[datetime]]) -> None:
    for n, (title, serves, hinders, *_) in enumerate(_ACTIVITIES):
        times = seen.get(n)
        if not times:
            continue
        id = f"act-{n + 1:04d}"
        _write(model / "activities" / f"{id}.md", ActivityMeta(
            id=id, title=title, part_of=[f"str-{s + 1:04d}" for s in serves],
            hinders=[f"str-{s + 1:04d}" for s in hinders], confidence=3 + n % 7,
            status="established" if len(times) > 60 else "tentative", first_seen=times[0], last_seen=times[-1]),
            f"{title}: {len(times)} actions so far, first on {times[0]:%B %-d} and last on {times[-1]:%B %-d}.\n\n"
            f"The first of them was {_name(times[0]).split('/', 1)[1]}.")


def _images(root: Path) -> dict[str, list[Path]]:
    # Two placeholder screenshots per kind of screen, linked into every interaction's folder.
    folder = root / "images"
    folder.mkdir()
    out: dict[str, list[Path]] = {}
    for scene in _SCENES:
        base = _png(scene)
        out[scene] = []
        for variant in range(2):
            path = folder / f"{scene}-{variant}.png"
            path.write_bytes(base if variant == 0 else _png("browser" if scene != "browser" else "notes"))
            out[scene].append(path)
    return out


def _write(path: Path, meta: BaseModel, body: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(meta, body), encoding="utf-8")


def _git(model: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(model), *args], check=True)
