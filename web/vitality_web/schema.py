"""What the API returns: one pydantic model per response shape (web spec, section 4).

These models are the contract with the app: FastAPI publishes them as its OpenAPI schema, from which
`app/src/api/schema.ts` is generated. Times are strings, exactly as the files hold them (ISO 8601
local time with its offset); the app shows the wall-clock text before the offset.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, RootModel


class Relations(BaseModel):
    conflicts_with: list[str]
    facilitates: list[str]
    serves: list[str]


class Striving(BaseModel):
    id: str
    title: str
    confidence: int
    needs: list[str]
    orientation: str
    aspiration_vs_obligation: str
    autonomy: str
    relations: Relations
    locked: bool
    body: str


class Activity(BaseModel):
    id: str
    title: str
    part_of: list[str]
    hinders: list[str]
    confidence: int
    status: str             # "tentative" or "established"
    first_seen: str
    last_seen: str
    locked: bool
    body: str


class Action(BaseModel):
    """An action as the model lists it: without its body or operations, which `ActionDetail` adds."""

    id: str                 # "2026-11-03/14-22-05"
    summary: str
    start: str
    end: str
    interactions: list[str]  # the interactions it cites, in the order of its operations
    part_of: list[str]
    confidence: int


class ActionDetail(Action):
    operations: list[str]   # "<interaction>#<id>"
    body: str


class InteractionSummary(BaseModel):
    """What the views need about one interaction without fetching its evidence."""

    start: str
    end: str
    operations: int
    screenshots: int
    first_operation: str    # the text of its first operation (the graph's label)


class Interactions(RootModel[dict[str, InteractionSummary]]):
    """Interaction summaries by name (`GET /api/interactions`)."""


class Problem(BaseModel):
    path: str               # relative to the model folder
    error: str


class Model(BaseModel):
    strivings: list[Striving]
    activities: list[Activity]
    actions: list[Action]                       # by start
    problems: list[Problem]                     # including cited interactions that are missing or malformed


class Operation(BaseModel):
    id: str
    text: str
    confidence: int


class Observation(BaseModel):
    device: str
    start: str
    end: str
    body: str


class Shot(BaseModel):
    """One screenshot of an interaction, with the screen it shows when the app captured several."""

    file: str                       # "0004.webp", in the interaction's captures folder
    screen_name: str | None = None  # the owner's name for the screen; None with one screen
    passive: bool = False           # taken while no one used the device


class Evidence(BaseModel):
    interaction: str
    start: str
    end: str
    observation: Observation | None   # None when the observer wrote none, or it is malformed
    operations: list[Operation]
    screenshots: list[str]            # file names in the interaction's captures folder, sorted
    shots: list[Shot]                 # the same, with their screens (from `interaction.json`, when it says)
    actions: list[str]                # ids of the actions citing it


class DayInteraction(BaseModel):
    """One observed interaction of a day, cited by an action or not (Day's Evidence mode)."""

    interaction: str
    device: str
    start: str
    end: str
    operations: int                   # 0 when its operations file is missing or malformed
    first_operation: str | None
    shots: list[Shot]
    actions: list[str]                # ids of the actions citing it; none: not modeled yet


class DayInteractions(BaseModel):
    day: str
    interactions: list[DayInteraction]   # every device's, by start


class InteractionDays(RootModel[dict[str, int]]):
    """How many interactions were observed on each local day (`GET /api/interaction-days`)."""


class SchedulerState(BaseModel):
    """The modeler's `status.json`; `not running` when no scheduler holds its lock."""

    model_config = ConfigDict(extra="allow")

    state: str              # idle, running, paused, waiting-limit, waiting-edits, not running
    since: str | None = None
    until: str | None = None


class Session(BaseModel):
    """One line of the modeler's `sessions.jsonl` (`offered` dropped); fields the app shows are named."""

    model_config = ConfigDict(extra="allow")

    run: str
    started: str
    ended: str
    outcome: str            # committed, reverted, failed, limited
    changes: dict[str, int] = {}
    cost: float | None = None


class Modeler(BaseModel):
    status: SchedulerState
    waiting: int | None     # operations waiting for the modeler; None if the model is not a git repo
    sessions: list[Session]  # newest last



class Pipeline(BaseModel):
    """How far the evidence has come: the clock, and the newest capture and observation (Home's header)."""

    now: str                        # the server's time, local with its offset (fixed by `serve_fixture.py --now`)
    last_capture: str | None        # UTC, from the newest `<device>/<day>/<time>` folder of the captures
    last_observation: str | None    # UTC, from the newest file under the model's `observations/`


class ChangeItem(BaseModel):
    """One change to a striving or activity in a modeler commit, read from its frontmatter before and after."""

    kind: Literal["created", "retitled", "status", "confidence", "serves", "unserves", "removed"]
    level: Literal["striving", "activity"]
    id: str
    title: str                      # after the commit; before it, if removed
    before: str | None = None       # retitled, status, confidence: the old value
    after: str | None = None        # ... and the new one
    other: str | None = None        # serves / unserves: the striving; removed: the activity it was merged into, if seen


class ModelerCommit(BaseModel):
    """A commit of the modeler (`modeler run <session>: ...`) and what it changed."""

    commit: str
    time: str                       # committer time, local with its offset
    session: str                    # the run id, as in `sessions.jsonl`
    actions_added: int
    actions_changed: int
    items: list[ChangeItem]


class Changes(BaseModel):
    commits: list[ModelerCommit]    # newest first
    more: bool                      # whether older modeler commits remain
