"""What the API returns: one pydantic model per response shape (web spec, section 4).

These models are the contract with the app: FastAPI publishes them as its OpenAPI schema, from which
`app/src/api/schema.ts` is generated. Times are strings, exactly as the files hold them (ISO 8601
local time with its offset); the app shows the wall-clock text before the offset.
"""

from pydantic import BaseModel, ConfigDict


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
    id: str                 # "2026-11-03/14-22-05"
    summary: str
    start: str
    end: str
    operations: list[str]   # "<interaction>#<id>"
    part_of: list[str]
    confidence: int
    body: str


class InteractionSummary(BaseModel):
    """What the views need about one interaction without fetching its evidence."""

    start: str
    end: str
    operations: int
    screenshots: int
    first_operation: str    # the text of its first operation (the graph's label)


class Problem(BaseModel):
    path: str               # relative to the model folder
    error: str


class Model(BaseModel):
    strivings: list[Striving]
    activities: list[Activity]
    actions: list[Action]                       # by start
    interactions: dict[str, InteractionSummary]  # every interaction an action cites, by name
    problems: list[Problem]


class Operation(BaseModel):
    id: str
    text: str
    confidence: int


class Observation(BaseModel):
    device: str
    start: str
    end: str
    body: str


class Evidence(BaseModel):
    interaction: str
    start: str
    end: str
    observation: Observation | None   # None when the observer wrote none, or it is malformed
    operations: list[Operation]
    screenshots: list[str]            # file names in the interaction's captures folder, sorted
    actions: list[str]                # ids of the actions citing it


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

