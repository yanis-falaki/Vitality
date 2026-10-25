# Server Ingest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record the laptop's captures as interaction folders, upload each finished interaction over Tailscale, and store it on a FastAPI server on the exe.dev VM.

**Architecture:** A new pure package `protocol/` (`vitality_protocol`) holds the folder layout and the Pydantic upload contract. The daemon's policy now emits `Snapshot` and `InteractionEnd` actions; the writer stores `captures/<YYYY-MM-DD>/<HH-MM-SS>/<offset>.webp` plus `interaction.json`; a new `daemon/upload/` uploader posts finished interactions with `httpx`. The new `server/` (`vitality_server`) validates and stores them atomically, identifying devices through Tailscale.

**Tech Stack:** Python 3.14, uv, Pydantic v2, FastAPI + uvicorn + python-multipart, httpx, SQLite, pytest + pytest-asyncio, pyright (standard mode).

**Spec:** `docs/superpowers/specs/2026-10-25-server-ingest-design.md`

## Global Constraints

- Packages: `protocol/` (`vitality-protocol`, import `vitality_protocol`), `server/` (`vitality-server`, import `vitality_server`); `client/` and `server/` depend on `protocol/` via a uv path source; `protocol/` depends only on `pydantic`.
- `requires-python = ">=3.12"` in all three; run with Python 3.14.
- Interaction names: UTC start, `YYYY-MM-DD/HH-MM-SS`, clash suffix `-2`, `-3`, …
- Shot files: four-digit whole seconds since `start`, `.webp` (`0004.webp`), clash suffix `0004-2.webp`.
- `interaction.json` is written last; its presence means the folder is complete.
- `start` = first input (epoch → UTC); the shot at `0000` is the screen just before it.
- Stored timestamps: UTC ISO 8601 with milliseconds and `Z` (existing `format_ts`).
- Upload: `POST /interactions`, multipart form field `interaction` (the `Interaction` JSON) + file parts `shots`; replies `201`/`200` `UploadReply`, errors `ErrorReply`; idempotent.
- Health: `GET /health` → `Health`.
- Client config key: `upload_server` (string URL, e.g. `http://vitality-librarian:8100`); unset = upload disabled.
- Server config `/etc/vitality/server.toml`: `data_dir = "/srv/vitality"`, `port = 8100`, `allowed_clients = ["laptop"]`.
- Server binds only to the VM's Tailscale IPv4 address.
- Upload cap: 50 MiB per request (`413`).
- Retry: 5 s doubling to 300 s on connection error/timeout/5xx/507; 300 s on 403; idle wait 60 s.
- Logs never contain image contents, key codes or pointer data.
- No `Co-Authored-By` or "Generated with" lines in commits.
- Every class with behaviour has a Protocol (package `interface.py`, or a `# == Contract ==` section at the top of a single file); callbacks and messages appear in interface signatures.
- Pyright: 0 errors in each package (`uv run pyright` in `client/`, `protocol/`, `server/`).

## Review Focus

1. **A failed first-shot write must not leave an empty interaction.** If encoding or the index insert fails for an interaction's first shot, no `interactions` row may remain (it would later produce an `interaction.json` with no shots, which the server rejects). → Task 4 test `test_failed_first_shot_leaves_no_interaction`.
2. **A backwards clock jump that reuses a start second** must give the second interaction a `-2` folder, and the server must accept the suffixed name. → Task 4 `test_name_clash_gets_suffix`; Task 8 `test_suffixed_name_is_accepted`.
3. **Re-uploading after a client change in JSON formatting** (e.g. `+00:00` instead of `Z`, or different key order) must still be `200 existing`, not `409`: compare parsed models, not bytes. → Task 8 `test_same_interaction_different_json_formatting_is_existing`.
4. **A laptop with no resolvable timezone** (no `/etc/localtime` symlink, no `TZ`) must still record interactions, with `tz = "UTC"`. → Task 4 `test_local_tz_name_falls_back_to_utc`.
5. **A manual capture while paused** must become a one-shot interaction marked `manual` and still upload (uploads continue while paused). → Task 3 `test_manual_while_paused_is_one_shot_interaction`; Task 6 `test_uploads_continue_while_paused`.

---

### Task 1: Protocol package

**Files:**
- Create: `protocol/pyproject.toml`, `protocol/vitality_protocol/__init__.py`, `protocol/vitality_protocol/layout.py`, `protocol/vitality_protocol/messages.py`, `protocol/vitality_protocol/routes.py`
- Test: `protocol/tests/test_layout.py`, `protocol/tests/test_messages.py`

**Interfaces:**
- Produces (`vitality_protocol.layout`):
  - `INTERACTION_FILE: str = "interaction.json"`
  - `INTERACTION_NAME_RE: re.Pattern` — `^\d{4}-\d{2}-\d{2}/\d{2}-\d{2}-\d{2}(-[1-9]\d*)?$`
  - `SHOT_FILE_RE: re.Pattern` — `^\d{4,}(-[1-9]\d*)?\.webp$`
  - `interaction_name(start: datetime, taken: Container[str] = ()) -> str` (start must be timezone-aware; converted to UTC)
  - `name_matches_start(name: str, start: datetime) -> bool`
  - `shot_filename(offset_s: int, taken: Container[str] = ()) -> str` (raises `ValueError` if `offset_s < 0`)
- Produces (`vitality_protocol.messages`): `Shot`, `Interaction`, `UploadReply`, `ErrorReply`, `Health` exactly as spec §5.2, all `model_config = ConfigDict(extra="forbid", frozen=True)`; datetimes are `pydantic.AwareDatetime`; `Shot.file` must match `SHOT_FILE_RE`; `Interaction.name` must match `INTERACTION_NAME_RE`; `Interaction.shots` has `min_length=1`.
- Produces (`vitality_protocol.routes`): `INTERACTIONS_PATH = "/interactions"`, `HEALTH_PATH = "/health"`, `INTERACTION_FIELD = "interaction"`, `SHOTS_FIELD = "shots"`, `MAX_UPLOAD_BYTES = 50 * 1024 * 1024`; module docstring = the route table from spec §5.3, including the idempotency note.
- `__init__.py` re-exports all of the above and its docstring lists the three modules.

- [ ] **Step 1: Create `protocol/pyproject.toml`**

Hatchling build; `name = "vitality-protocol"`, `version = "0.1.0"`, `requires-python = ">=3.12"`, `dependencies = ["pydantic>=2.9"]`; dev group `pytest`, `pyright`; `[tool.pyright]` `include = ["vitality_protocol", "tests"]`, `venvPath = "."`, `venv = ".venv"`, `typeCheckingMode = "standard"`. Run `cd protocol && uv venv --python 3.14 && uv sync`.

- [ ] **Step 2: Write the failing tests**

```python
# protocol/tests/test_layout.py
from datetime import UTC, datetime, timedelta, timezone
import pytest
from vitality_protocol.layout import interaction_name, name_matches_start, shot_filename

T = datetime(2026, 10, 25, 17, 0, 0, 449000, tzinfo=UTC)

def test_interaction_name_is_utc_start_to_the_second():
    assert interaction_name(T) == "2026-10-25/17-00-00"

def test_interaction_name_converts_local_time_to_utc():
    local = T.astimezone(timezone(timedelta(hours=-7)))
    assert interaction_name(local) == "2026-10-25/17-00-00"

def test_interaction_name_suffix_on_clash():
    assert interaction_name(T, {"2026-10-25/17-00-00"}) == "2026-10-25/17-00-00-2"
    assert interaction_name(T, {"2026-10-25/17-00-00", "2026-10-25/17-00-00-2"}) == "2026-10-25/17-00-00-3"

def test_interaction_name_rejects_naive_datetime():
    with pytest.raises(ValueError):
        interaction_name(datetime(2026, 10, 25, 17, 0, 0))

def test_name_matches_start_allows_suffix():
    assert name_matches_start("2026-10-25/17-00-00", T)
    assert name_matches_start("2026-10-25/17-00-00-2", T)
    assert not name_matches_start("2026-10-25/17-00-01", T)

def test_shot_filename():
    assert shot_filename(0) == "0000.webp"
    assert shot_filename(14) == "0014.webp"
    assert shot_filename(14, {"0014.webp"}) == "0014-2.webp"
    with pytest.raises(ValueError):
        shot_filename(-1)
```

```python
# protocol/tests/test_messages.py
import pytest
from pydantic import ValidationError
from vitality_protocol.messages import Interaction, Shot, UploadReply

SHOT = {"file": "0000.webp", "ts": "2026-10-25T17:00:00.449Z", "trigger": "activity",
        "width": 1920, "height": 1080, "bytes": 104052}
INTERACTION = {"name": "2026-10-25/17-00-00", "source": "screen",
               "start": "2026-10-25T17:00:00.449Z", "end": "2026-10-25T17:00:04.120Z",
               "local_start": "2026-10-25T10:00:00.449-07:00", "tz": "America/Vancouver",
               "shots": [SHOT]}

def test_interaction_round_trip():
    i = Interaction.model_validate(INTERACTION)
    assert Interaction.model_validate_json(i.model_dump_json()) == i
    assert i.shots[0].ts.utcoffset().total_seconds() == 0

@pytest.mark.parametrize("change", [
    {"name": "01M5R9694KWX42H5VG73MBPDNF"},
    {"source": "mic"},
    {"shots": []},
    {"start": "2026-10-25T17:00:00"},          # naive
    {"extra": 1},
])
def test_invalid_interaction_rejected(change):
    with pytest.raises(ValidationError):
        Interaction.model_validate({**INTERACTION, **change})

@pytest.mark.parametrize("change", [{"file": "../x.webp"}, {"trigger": "before"}])
def test_invalid_shot_rejected(change):
    with pytest.raises(ValidationError):
        Shot.model_validate({**SHOT, **change})

def test_upload_reply_defaults_ok():
    assert UploadReply(stored="new").model_dump() == {"ok": True, "stored": "new"}
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd protocol && uv run pytest -q`
Expected: FAIL (`ModuleNotFoundError: vitality_protocol.layout`)

- [ ] **Step 4: Implement `layout.py`, `messages.py`, `routes.py`, `__init__.py`**

`interaction_name`: raise `ValueError` if `start.tzinfo is None`; format `start.astimezone(UTC)` with `%Y-%m-%d/%H-%M-%S`; append `-n` from 2 upward while the candidate is in `taken`. `name_matches_start`: strip a trailing `-<n>` suffix (only when the name matches `INTERACTION_NAME_RE`) and compare with `interaction_name(start)`. `shot_filename`: `f"{offset_s:04d}"`, same suffix rule before `.webp`.

- [ ] **Step 5: Run tests and pyright**

Run: `cd protocol && uv run pytest -q && uv run pyright`
Expected: all pass; `0 errors`

- [ ] **Step 6: Commit**

```bash
git add protocol/
git commit -m "protocol: interaction layout and upload contract"
```

---

### Task 2: Client config gains `upload_server`

**Files:**
- Modify: `client/vitality_client/shared/config.py` (`Config`, `load_config`, `_check_range`)
- Test: `client/tests/test_config.py`

**Interfaces:**
- Produces: `Config.upload_server: str | None = None`.

- [ ] **Step 1: Write the failing tests**

```python
def test_upload_server_loaded(tmp_path):
    p = tmp_path / "client.toml"; p.write_text('upload_server = "http://vitality-librarian:8100"\n')
    assert load_config(p).upload_server == "http://vitality-librarian:8100"

def test_upload_server_defaults_to_none(tmp_path):
    assert load_config(tmp_path / "missing.toml").upload_server is None

@pytest.mark.parametrize("value", ['8100', '"vitality-librarian:8100"', '""'])
def test_upload_server_must_be_http_url(tmp_path, value):
    p = tmp_path / "client.toml"; p.write_text(f"upload_server = {value}\n")
    with pytest.raises(ValueError, match="upload_server"):
        load_config(p)
```

- [ ] **Step 2: Run to verify failure**

Run: `cd client && uv run pytest tests/test_config.py -q`
Expected: FAIL (`unknown config key: upload_server`)

- [ ] **Step 3: Implement**

`load_config` handles a third field type: `str | None` fields must be TOML strings starting with `http://` or `https://` (error message names the key); a trailing `/` is stripped.

- [ ] **Step 4: Run tests**

Run: `cd client && uv run pytest tests/test_config.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add client/vitality_client/shared/config.py client/tests/test_config.py
git commit -m "client: upload_server config key"
```

---

### Task 3: Policy emits interaction boundaries

**Files:**
- Modify: `client/vitality_client/daemon/policy/interface.py`, `capture_policy.py`, `__init__.py`
- Test: `client/tests/test_policy.py` (rewrite expectations to the new actions)

**Interfaces:**
- Produces (`vitality_client.daemon.policy`):

```python
@dataclass(frozen=True)
class InteractionRef:
    start: float                      # first input, epoch seconds

@dataclass(frozen=True)
class Snapshot:
    interaction: InteractionRef
    trigger: Literal["activity", "manual"]
    ts: float

@dataclass(frozen=True)
class InteractionEnd:
    interaction: InteractionRef
    ts: float

Action = Snapshot | InteractionEnd
```

  `SnapshotPolicy` methods `on_activity`, `on_tick`, `pause` return `list[Action]`; new `manual(self, now: float) -> list[Action]`. `CapturePolicy(settle_seconds, max_gap_seconds, paused=False, paused_until=None)` — the `new_id` parameter is removed. `SnapshotRequest` is deleted. `__init__.py` docstring restates the rules with the new actions.

- [ ] **Step 1: Rewrite the tests to the new actions**

```python
from vitality_client.daemon.policy import CapturePolicy, InteractionEnd, InteractionRef, Snapshot

def P(**kw): return CapturePolicy(2, 10, **kw)
R = InteractionRef(100.0)

def test_idle_activity_starts_interaction():
    p = P(); assert p.on_activity(100.0) == [Snapshot(R, "activity", 100.0)]; assert p.state == "active"

def test_settle_emits_final_snapshot_and_end():
    p = P(); p.on_activity(100.0); p.on_activity(101.0)
    assert p.on_tick(102.9) == []
    assert p.on_tick(103.0) == [Snapshot(R, "activity", 103.0), InteractionEnd(R, 103.0)]
    assert p.state == "idle"

def test_continuous_activity_snapshots_every_max_gap():
    p = P(); out = []
    for t in range(25): out += p.on_activity(100.0 + t)
    assert [a.ts for a in out] == [100.0, 110.0, 120.0]
    assert all(isinstance(a, Snapshot) and a.interaction == R for a in out)

def test_pause_while_active_ends_interaction():
    p = P(); p.on_activity(100.0)
    assert p.pause(101.0) == [Snapshot(R, "activity", 101.0), InteractionEnd(R, 101.0)]
    assert p.on_activity(102.0) == [] and p.state == "paused"

def test_pause_right_after_settle_does_not_end_twice():
    p = P(); p.on_activity(100.0)
    out = p.pause(103.0)
    assert out == [Snapshot(R, "activity", 103.0), InteractionEnd(R, 103.0)]

def test_manual_while_active_joins_open_interaction():
    p = P(); p.on_activity(100.0)
    assert p.manual(101.5) == [Snapshot(R, "manual", 101.5)]
    assert p.next_deadline() == 102.0          # timers unchanged

def test_manual_while_idle_is_one_shot_interaction():
    r = InteractionRef(50.0)
    assert P().manual(50.0) == [Snapshot(r, "manual", 50.0), InteractionEnd(r, 50.0)]

def test_manual_while_paused_is_one_shot_interaction():                     # Review Focus 5
    p = P(paused=True); r = InteractionRef(50.0)
    assert p.manual(50.0) == [Snapshot(r, "manual", 50.0), InteractionEnd(r, 50.0)]
    assert p.state == "paused"
```

Keep the existing pause/timed-pause/next-deadline/clock-backwards tests, updating only their expected values to `Snapshot`/`InteractionEnd`.

- [ ] **Step 2: Run to verify failure**

Run: `cd client && uv run pytest tests/test_policy.py -q`
Expected: FAIL (`ImportError: cannot import name 'InteractionEnd'`)

- [ ] **Step 3: Implement**

The open interaction is held as an `InteractionRef` created on the first activity after idle. Settle returns `[Snapshot(..., now), InteractionEnd(..., now)]`; the max-gap path returns one `Snapshot`; `pause` while active appends the final pair unless `_process_due` already ended the interaction. `manual` never changes `state` or timers.

- [ ] **Step 4: Run tests**

Run: `cd client && uv run pytest tests/test_policy.py -q`
Expected: PASS (other test files fail until Task 5; that is expected)

- [ ] **Step 5: Commit**

```bash
git add client/vitality_client/daemon/policy client/tests/test_policy.py
git commit -m "client: policy emits Snapshot and InteractionEnd actions"
```

---

### Task 4: Storage writes interaction folders

**Files:**
- Modify: `client/vitality_client/daemon/storage/interface.py`, `index.py`, `writer.py`, `__init__.py`
- Modify: `client/pyproject.toml` (add `vitality-protocol` path dependency)
- Test: `client/tests/test_store.py`, `client/tests/test_writer.py` (rewrite)

**Interfaces:**
- Consumes: `vitality_protocol.layout` (`interaction_name`, `shot_filename`, `INTERACTION_FILE`), `vitality_protocol.messages.Interaction` (Task 1).
- Produces (`vitality_client.daemon.storage`):

```python
@dataclass(frozen=True)
class ShotRow:
    interaction: str                 # "2026-10-25/17-00-00"
    file: str                        # "0004.webp"
    ts: str                          # format_ts
    trigger: Literal["activity", "manual"]
    width: int
    height: int
    bytes: int
    @property
    def path(self) -> str: ...       # "captures/<interaction>/<file>"

@dataclass(frozen=True)
class InteractionRow:
    name: str
    start: str                       # format_ts
    local_start: str                 # ISO 8601 with offset and milliseconds
    tz: str
    source: str = "screen"

class CaptureIndex(Protocol):
    def insert_shot(self, shot: ShotRow, new_interaction: InteractionRow | None = None) -> None: ...  # one transaction
    def has_interaction(self, name: str) -> bool: ...
    def shot_files(self, name: str) -> set[str]: ...
    def end_interaction(self, name: str) -> None: ...        # end = its last shot's ts
    def open_interactions(self) -> list[str]: ...            # end IS NULL
    def interaction(self, name: str) -> Interaction: ...
    def shot_paths(self) -> set[str]: ...
    def count_shots_since(self, ts: str) -> int: ...
    def last_shot_ts(self) -> str | None: ...
    def pending_uploads(self) -> list[str]: ...              # ended, not uploaded, no error; oldest first
    def pending_count(self) -> int: ...
    def mark_uploaded(self, name: str, ts: str) -> None: ...
    def mark_failed(self, name: str, error: str) -> None: ...
    def close(self) -> None: ...

class CaptureWriter(Protocol):
    def write(self, image: Image.Image, start: float, ts: float,
              trigger: Literal["activity", "manual"]) -> ShotRow: ...
    def end(self, start: float) -> str | None: ...           # writes interaction.json; None if the interaction has no shots
    def close_interrupted(self) -> int: ...                  # startup: end + write json for open interactions
    def cleanup_orphans(self) -> int: ...
    def disk_usage_bytes(self) -> int: ...
```

  `Writer(data_dir: Path, store: CaptureIndex, quality: int, tz: str)`; module function `local_tz_name() -> str`; `format_ts` unchanged. `CaptureRow` is deleted. Schema: spec §6.3 exactly, plus `CREATE INDEX` on `shots(ts)` and `interactions(uploaded_at)`.

- [ ] **Step 1: Add the dependency**

In `client/pyproject.toml`: `dependencies` gains `"vitality-protocol"`; add `[tool.uv.sources] vitality-protocol = { path = "../protocol", editable = true }`. Run `cd client && uv sync`.

- [ ] **Step 2: Write the failing tests**

```python
# client/tests/test_writer.py (replaces the old file)
import json, sqlite3
from unittest.mock import patch
import pytest
from PIL import Image
from vitality_client.daemon.storage import Store, Writer, local_tz_name

img = Image.new("RGB", (64, 40), "white")
T0 = 1761411600.449                      # 2025-10-25T17:00:00.449Z

def make(tmp_path):
    store = Store(tmp_path / "index.sqlite")
    return store, Writer(tmp_path, store, 85, "America/Vancouver")

def test_shots_go_into_interaction_folder(tmp_path):
    store, w = make(tmp_path)
    a = w.write(img, T0, T0, "activity"); b = w.write(img, T0, T0 + 4.7, "activity")
    assert (a.path, b.path) == ("captures/2025-10-25/17-00-00/0000.webp",
                                "captures/2025-10-25/17-00-00/0004.webp")
    assert Image.open(tmp_path / b.path).format == "WEBP"

def test_end_writes_interaction_json(tmp_path):
    store, w = make(tmp_path)
    w.write(img, T0, T0, "activity"); w.write(img, T0, T0 + 4.7, "manual")
    assert w.end(T0) == "2025-10-25/17-00-00"
    data = json.loads((tmp_path / "captures/2025-10-25/17-00-00/interaction.json").read_text())
    assert data["tz"] == "America/Vancouver" and data["local_start"].startswith("2025-10-25T10:00:00.449-07:00")
    assert [s["file"] for s in data["shots"]] == ["0000.webp", "0004.webp"]
    assert [s["trigger"] for s in data["shots"]] == ["activity", "manual"]
    assert data["end"] == "2025-10-25T17:00:05.149Z"
    assert store.pending_uploads() == ["2025-10-25/17-00-00"]

def test_end_without_shots_returns_none(tmp_path):
    _, w = make(tmp_path)
    assert w.end(T0) is None and not (tmp_path / "captures").exists()

def test_same_second_shot_gets_suffix(tmp_path):
    _, w = make(tmp_path)
    w.write(img, T0, T0 + 4.1, "activity")
    assert w.write(img, T0, T0 + 4.6, "manual").file == "0004-2.webp"

def test_name_clash_gets_suffix(tmp_path):                                   # Review Focus 2
    _, w = make(tmp_path)
    w.write(img, T0, T0, "activity"); w.end(T0)
    assert w.write(img, T0 + 0.3, T0 + 0.3, "activity").interaction == "2025-10-25/17-00-00-2"

def test_failed_first_shot_leaves_no_interaction(tmp_path):                 # Review Focus 1
    store, w = make(tmp_path)
    with patch.object(store, "insert_shot", side_effect=sqlite3.OperationalError("disk full")):
        with pytest.raises(sqlite3.OperationalError): w.write(img, T0, T0, "activity")
    assert not store.has_interaction("2025-10-25/17-00-00") and w.end(T0) is None

def test_close_interrupted_ends_open_interactions(tmp_path):
    store, w = make(tmp_path)
    w.write(img, T0, T0, "activity")
    store2 = Store(tmp_path / "index.sqlite"); w2 = Writer(tmp_path, store2, 85, "UTC")
    assert w2.close_interrupted() == 1
    assert (tmp_path / "captures/2025-10-25/17-00-00/interaction.json").exists()
    assert store2.open_interactions() == []

def test_cleanup_removes_unindexed_files_and_empty_folders(tmp_path):
    _, w = make(tmp_path)
    stray = tmp_path / "captures/2025-10-25/17-09-09/0000.webp"; stray.parent.mkdir(parents=True)
    stray.write_bytes(b"x"); (stray.parent / "0001.webp.tmp").write_bytes(b"x")
    assert w.cleanup_orphans() == 2 and not stray.parent.exists()

def test_local_tz_name_falls_back_to_utc(monkeypatch, tmp_path):            # Review Focus 4
    monkeypatch.delenv("TZ", raising=False)
    with patch("vitality_client.daemon.storage.writer.LOCALTIME", tmp_path / "nope"):
        assert local_tz_name() == "UTC"
```

Keep the existing oversized-image, failed-encode, disk-usage and `format_ts` tests, adapted to the new `write` signature.

```python
# client/tests/test_store.py (replaces the old file)
from vitality_client.daemon.storage import InteractionRow, ShotRow, Store

I = InteractionRow("2025-10-25/17-00-00", "2025-10-25T17:00:00.449Z",
                   "2025-10-25T10:00:00.449-07:00", "America/Vancouver")
S = ShotRow(I.name, "0000.webp", "2025-10-25T17:00:00.449Z", "activity", 1920, 1080, 1000)

def test_pending_upload_lifecycle(tmp_path):
    s = Store(tmp_path / "i.sqlite"); s.insert_shot(S, I)
    assert s.pending_uploads() == []                 # still open
    s.end_interaction(I.name); assert s.pending_uploads() == [I.name] and s.pending_count() == 1
    s.mark_uploaded(I.name, "2025-10-25T17:01:00.000Z"); assert s.pending_uploads() == []

def test_failed_interaction_is_not_pending(tmp_path):
    s = Store(tmp_path / "i.sqlite"); s.insert_shot(S, I); s.end_interaction(I.name)
    s.mark_failed(I.name, "422: bad name"); assert s.pending_uploads() == []

def test_pending_oldest_first(tmp_path):
    s = Store(tmp_path / "i.sqlite")
    late = InteractionRow("2025-10-25/18-00-00", "2025-10-25T18:00:00.000Z", I.local_start, I.tz)
    s.insert_shot(ShotRow(late.name, "0000.webp", late.start, "activity", 1, 1, 1), late)
    s.insert_shot(S, I); s.end_interaction(late.name); s.end_interaction(I.name)
    assert s.pending_uploads() == [I.name, late.name]

def test_interaction_model(tmp_path):
    s = Store(tmp_path / "i.sqlite"); s.insert_shot(S, I); s.end_interaction(I.name)
    m = s.interaction(I.name)
    assert m.name == I.name and m.end == m.start and [x.file for x in m.shots] == ["0000.webp"]

def test_counts_and_paths(tmp_path):
    s = Store(tmp_path / "i.sqlite"); s.insert_shot(S, I)
    assert s.count_shots_since("2025-10-25T00:00:00.000Z") == 1 and s.last_shot_ts() == S.ts
    assert s.shot_paths() == {"captures/2025-10-25/17-00-00/0000.webp"}
```

- [ ] **Step 3: Run to verify failure**

Run: `cd client && uv run pytest tests/test_store.py tests/test_writer.py -q`
Expected: FAIL (`ImportError: cannot import name 'InteractionRow'`)

- [ ] **Step 4: Implement the index, writer and exports**

`Writer` keeps `dict[float, str]` from `start` to folder name for interactions it opened this run. On the first `write` for a `start`, the name comes from `interaction_name(datetime.fromtimestamp(start, UTC), taken)`, where `taken` is a small `Container[str]` whose `__contains__` calls `store.has_interaction`; the `InteractionRow` is passed to `insert_shot` so row and first shot commit together. Offset = `int(ts - start)` floored at 0. `local_start` = `datetime.fromtimestamp(start, ZoneInfo(tz)).isoformat(timespec="milliseconds")`. `end` and `close_interrupted` write `interaction.json` as `store.interaction(name).model_dump_json(indent=2)` via a temp file + `os.replace`. `LOCALTIME = Path("/etc/localtime")`; `local_tz_name()` returns `TZ` if it is a valid `ZoneInfo` key, else the part of `LOCALTIME.resolve()` after `zoneinfo/`, else `"UTC"`. `cleanup_orphans` deletes `.tmp` files and `.webp` files not in `shot_paths()`, then removes empty interaction folders. Update the package docstring to the new layout.

- [ ] **Step 5: Run tests**

Run: `cd client && uv run pytest tests/test_store.py tests/test_writer.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add client/pyproject.toml client/uv.lock client/vitality_client/daemon/storage client/tests/test_store.py client/tests/test_writer.py
git commit -m "client: interaction folders and two-table index"
```

---

### Task 5: Daemon wiring for interactions

**Files:**
- Modify: `client/vitality_client/daemon/daemon.py`, `client/vitality_client/shared/channel.py` (`UploadStatus`, `Status.upload`)
- Create: `client/vitality_client/daemon/upload/__init__.py`, `client/vitality_client/daemon/upload/interface.py`
- Test: `client/tests/test_daemon.py` (update), `client/tests/test_cli.py` and `client/tests/test_control.py` (add `upload` to status fixtures)

**Interfaces:**
- Consumes: Task 3 actions; Task 4 `CaptureWriter`/`CaptureIndex`.
- Produces (`shared/channel.py`):

```python
class UploadStatus(TypedDict):
    state: Literal["disabled", "ok", "offline", "rejected"]
    pending: int
    last_upload: str | None          # UTC ISO timestamp
# Status gains:  upload: UploadStatus
```

- Produces (`daemon/upload/interface.py`):

```python
class Uploader(Protocol):
    async def run(self) -> None: ...     # until cancelled: upload pending interactions, oldest first
    def wake(self) -> None: ...          # an interaction just ended: check now
    @property
    def status(self) -> UploadStatus: ...
```

- Produces: `CaptureDaemon(config, paths, frames, activity, clock=time.time, uploader: Callable[[CaptureIndex], Uploader] | None = None)`. The `Daemon` Protocol docstring adds `uploader.run()` to the wiring list.

- [ ] **Step 1: Update the daemon tests**

Replace `sqlite_rows` with a helper reading `shots` joined to `interactions`. Changed expectations:

```python
async def test_activity_burst_writes_one_interaction(daemon_env):
    d, clock, data = daemon_env()
    d.handle_activity(100.0); clock.t = 102.0; await d.tick_once()
    folder = next((data / "captures").glob("*/*"))
    assert sorted(p.name for p in folder.iterdir()) == ["0000.webp", "0002.webp", "interaction.json"]

async def test_capture_command_joins_open_interaction(daemon_env):
    d, clock, data = daemon_env()
    d.handle_activity(100.0); clock.t = 101.0
    reply = await d.handle({"cmd": "capture"})
    assert reply["ok"] and reply["path"].endswith("/0001.webp")

async def test_capture_command_while_idle_is_one_shot_interaction(daemon_env):
    d, clock, data = daemon_env(); clock.t = 50.0
    await d.handle({"cmd": "capture"}); await d.drain()
    (meta,) = (data / "captures").glob("*/*/interaction.json")
    assert json.loads(meta.read_text())["shots"][0]["trigger"] == "manual"

async def test_interaction_end_wakes_uploader(daemon_env):
    woken = []
    class FakeUploader:
        status = {"state": "ok", "pending": 0, "last_upload": None}
        async def run(self): await asyncio.Event().wait()
        def wake(self): woken.append(True)
    d, clock, _ = daemon_env(uploader=lambda index: FakeUploader())
    d.handle_activity(100.0); clock.t = 102.0; await d.tick_once()
    assert woken == [True]

async def test_status_upload_disabled_without_uploader(daemon_env):
    d, _, _ = daemon_env()
    assert d.status()["upload"] == {"state": "disabled", "pending": 0, "last_upload": None}

async def test_run_closes_interrupted_interaction_at_startup(daemon_env):
    ...  # write one shot with a first daemon (no end), then run() a second daemon on the same data
         # dir until its server is up; assert interaction.json exists
```

`daemon_env`'s `make` gains an `uploader=None` parameter passed through. Every other existing test keeps its intent; replace `kind` assertions with file-name or `trigger` assertions. Add `"upload": {"state": "disabled", "pending": 0, "last_upload": None}` to `STATUS` in `test_cli.py` and any status dicts in `test_control.py`.

- [ ] **Step 2: Run to verify failure**

Run: `cd client && uv run pytest -q`
Expected: FAIL in `test_daemon.py`

- [ ] **Step 3: Implement**

Write-queue items become one of two internal frozen dataclasses: `_Shot(frame, start, ts, trigger, saved)` and `_End(start)`, processed in order by `_write_loop` (`writer.write` / `writer.end` in a thread). After `writer.end` returns a name, call `self._uploader.wake()` if there is one. `_step` routes `Snapshot` → `_snapshot` (reads the frame immediately, as today) and `InteractionEnd` → enqueue `_End`. `pause` uses the same routing. `_capture_now` calls `self._policy.manual(now)`, attaches the `saved` future to its `Snapshot`, and enqueues any `InteractionEnd` after it. `run()` calls `writer.close_interrupted()` right after `cleanup_orphans()`, and adds `uploader.run()` to the tasks (not to the critical set: an uploader crash is logged, not fatal). `status()` reports `upload` from the uploader, or `disabled` with `pending_count()`. `captures_today` and `last_capture` use `count_shots_since` / `last_shot_ts`. `Writer` gets `tz=local_tz_name()`.

- [ ] **Step 4: Run all client tests and pyright**

Run: `cd client && uv run pytest -q && uv run pyright`
Expected: all pass; `0 errors`

- [ ] **Step 5: Commit**

```bash
git add client/
git commit -m "client: daemon records interactions and reports upload status"
```

---

### Task 6: HTTP uploader

**Files:**
- Create: `client/vitality_client/daemon/upload/http_uploader.py`
- Modify: `client/vitality_client/daemon/upload/__init__.py` (export `HttpUploader`), `client/vitality_client/daemon/main.py`, `client/pyproject.toml` (add `httpx`)
- Test: `client/tests/test_uploader.py`

**Interfaces:**
- Consumes: `Uploader`, `UploadStatus` (Task 5); `CaptureIndex` (Task 4); `vitality_protocol.routes`, `UploadReply` (Task 1).
- Produces:

```python
class HttpUploader(Uploader):
    def __init__(self, server: str, index: CaptureIndex, data_dir: Path,
                 client: httpx.AsyncClient | None = None,
                 retry_initial: float = 5.0, retry_max: float = 300.0,
                 rejected_wait: float = 300.0, idle_wait: float = 60.0) -> None: ...
    async def upload_once(self) -> Literal["uploaded", "idle", "offline", "rejected", "failed"]: ...
```

  `run()` loops: `upload_once()`, then waits (until `wake()` or the timeout) for 0 s after `uploaded`/`failed`, the current backoff after `offline` (5 s doubling to 300 s, reset on success), `rejected_wait` after `rejected`, `idle_wait` after `idle`.

- [ ] **Step 1: Write the failing tests**

```python
# client/tests/test_uploader.py
import httpx, pytest
from PIL import Image
from vitality_client.daemon.storage import Store, Writer
from vitality_client.daemon.upload import HttpUploader

T0 = 1761411600.449

def setup(tmp_path, handler):
    store = Store(tmp_path / "i.sqlite"); w = Writer(tmp_path, store, 85, "UTC")
    w.write(Image.new("RGB", (8, 8)), T0, T0, "activity"); w.end(T0)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://srv")
    return store, HttpUploader("http://srv", store, tmp_path, client=client)

async def test_uploads_and_marks(tmp_path):
    seen = []
    def handler(req):
        seen.append(req); return httpx.Response(201, json={"ok": True, "stored": "new"})
    store, up = setup(tmp_path, handler)
    assert await up.upload_once() == "uploaded"
    assert seen[0].method == "POST" and seen[0].url.path == "/interactions"
    body = seen[0].content
    assert b'name="interaction"' in body and b'filename="0000.webp"' in body
    assert store.pending_uploads() == [] and up.status["state"] == "ok" and up.status["last_upload"]
    assert await up.upload_once() == "idle"

async def test_existing_counts_as_uploaded(tmp_path):
    store, up = setup(tmp_path, lambda r: httpx.Response(200, json={"ok": True, "stored": "existing"}))
    assert await up.upload_once() == "uploaded" and store.pending_uploads() == []

@pytest.mark.parametrize("response", [httpx.Response(503), httpx.Response(507, json={"ok": False, "error": "disk full"})])
async def test_server_trouble_is_offline_and_keeps_pending(tmp_path, response):
    store, up = setup(tmp_path, lambda r: response)
    assert await up.upload_once() == "offline" and store.pending_count() == 1 and up.status["state"] == "offline"

async def test_connection_error_is_offline(tmp_path):
    def handler(req): raise httpx.ConnectError("no route")
    store, up = setup(tmp_path, handler)
    assert await up.upload_once() == "offline" and store.pending_count() == 1

async def test_forbidden_is_rejected(tmp_path):
    store, up = setup(tmp_path, lambda r: httpx.Response(403, json={"ok": False, "error": "device not allowed"}))
    assert await up.upload_once() == "rejected" and up.status["state"] == "rejected" and store.pending_count() == 1

@pytest.mark.parametrize("code", [409, 422])
async def test_bad_interaction_is_skipped(tmp_path, code):
    store, up = setup(tmp_path, lambda r: httpx.Response(code, json={"ok": False, "error": "bad"}))
    assert await up.upload_once() == "failed" and store.pending_uploads() == []

async def test_missing_local_file_is_skipped(tmp_path):
    store, up = setup(tmp_path, lambda r: httpx.Response(201, json={"ok": True, "stored": "new"}))
    next(tmp_path.glob("captures/*/*/0000.webp")).unlink()
    assert await up.upload_once() == "failed"

async def test_wake_triggers_prompt_upload(tmp_path):
    ...  # run() with idle_wait=3600 in a task; add + end a second interaction; wake(); assert it is
         # uploaded within 1 s

async def test_uploads_continue_while_paused(tmp_path):                     # Review Focus 5
    ...  # daemon-level: daemon_env with a real HttpUploader on a MockTransport; pause, then
         # `capture`; drain; assert the one-shot manual interaction reaches the transport
```

- [ ] **Step 2: Run to verify failure**

Run: `cd client && uv run pytest tests/test_uploader.py -q`
Expected: FAIL (`ImportError: cannot import name 'HttpUploader'`)

- [ ] **Step 3: Implement**

Request: `client.post(INTERACTIONS_PATH, data={INTERACTION_FIELD: interaction.model_dump_json()}, files=[(SHOTS_FIELD, (shot.file, bytes, "image/webp")) ...])`, files read with `asyncio.to_thread`; timeout 30 s. `200`/`201` → `mark_uploaded(name, format_ts(time.time()))`; `403` → `rejected`; `409`/`422`/`413` and missing/unreadable local files → `mark_failed(name, reason)`, logged at error; connection errors, timeouts and other status codes → `offline`. Log one line per state change, never per retry. In `daemon/main.py`, pass `uploader=lambda index: HttpUploader(config.upload_server, index, paths.data_dir)` when `config.upload_server` is set.

- [ ] **Step 4: Run all client tests and pyright**

Run: `cd client && uv run pytest -q && uv run pyright`
Expected: all pass; `0 errors`

- [ ] **Step 5: Commit**

```bash
git add client/
git commit -m "client: upload finished interactions over HTTP"
```

---

### Task 7: CLI status line and doctor checks

**Files:**
- Modify: `client/vitality_client/cli/control.py` (`format_status`), `client/vitality_client/cli/checks.py` (`run_checks`)
- Test: `client/tests/test_cli.py`, `client/tests/test_doctor.py`

**Interfaces:**
- Consumes: `Status.upload` (Task 5); `Config.upload_server` (Task 2); `vitality_protocol.routes.HEALTH_PATH`.
- Produces: `check_tailscale(probe: Callable[[], bool]) -> Check`, `check_upload_server(server: str, probe: Callable[[str], bool]) -> Check`; `run_checks(env, paths, ..., tailscale_probe=tailscale_running, health_probe=server_healthy)`. Probes: `tailscale_running()` runs `tailscale status --json` and checks `BackendState == "Running"`; `server_healthy(server)` does a synchronous `httpx.get(server + HEALTH_PATH, timeout=5)` and validates `Health`.

- [ ] **Step 1: Write the failing tests**

```python
def test_format_status_upload_line():
    s = status(upload={"state": "offline", "pending": 12, "last_upload": "2026-10-25T16:58:02.000Z"})
    line = next(l for l in format_status(s).splitlines() if l.startswith("upload:"))
    assert line.startswith("upload: offline · 12 pending · last ")

def test_format_status_upload_disabled():
    assert "upload: disabled" in format_status(STATUS).splitlines()

def test_doctor_checks_upload_only_when_configured(tmp_path):
    # run_checks with fake probes and no upload_server: no "tailscale"/"upload server" checks;
    # with upload_server set in client.toml: both present, failing hints name the server URL
    ...
```

- [ ] **Step 2: Run to verify failure**

Run: `cd client && uv run pytest tests/test_cli.py tests/test_doctor.py -q`
Expected: FAIL

- [ ] **Step 3: Implement**

`format_status` adds `upload: disabled`, or `upload: <state> · <n> pending · last <local HH:MM:SS | never>`, after the `capture:` line. `run_checks` loads the config (a config error becomes a failing `config` check) and appends the two checks only when `upload_server` is set. Hints: `"start Tailscale: sudo systemctl enable --now tailscaled && sudo tailscale up"` and `"cannot reach <server>/health over Tailscale; is vitality-server running?"`.

- [ ] **Step 4: Run all client tests and pyright**

Run: `cd client && uv run pytest -q && uv run pyright`
Expected: all pass; `0 errors`

- [ ] **Step 5: Commit**

```bash
git add client/
git commit -m "client: status and doctor report uploads"
```

---

### Task 8: Server storage

**Files:**
- Create: `server/pyproject.toml`, `server/vitality_server/__init__.py`, `server/vitality_server/config.py`, `server/vitality_server/storage/__init__.py`, `server/vitality_server/storage/interface.py`, `server/vitality_server/storage/files.py`
- Test: `server/tests/test_config.py`, `server/tests/test_storage.py`

**Interfaces:**
- Consumes: Task 1 (`Interaction`, `name_matches_start`, `INTERACTION_FILE`).
- Produces:

```python
# config.py
@dataclass(frozen=True)
class ServerConfig:
    data_dir: Path = Path("/srv/vitality")
    port: int = 8100
    allowed_clients: tuple[str, ...] = ()
def load_server_config(path: Path) -> ServerConfig: ...   # unknown keys / wrong types → ValueError

# storage/interface.py
class InvalidUpload(Exception): ...         # → 422; str(exc) is the reason
class ConflictingUpload(Exception): ...     # → 409
class InteractionStore(Protocol):
    def put(self, client: str, interaction: Interaction,
            files: Mapping[str, bytes]) -> Literal["new", "existing"]: ...
    def disk_free_bytes(self) -> int: ...
    def recover(self) -> int: ...           # startup: delete tmp/, index complete folders missing from the index
    def close(self) -> None: ...

# storage/files.py
class FileStore(InteractionStore):
    def __init__(self, data_dir: Path, clock: Callable[[], float] = time.time) -> None: ...
```

  Server index schema: the laptop's two tables with a leading `client TEXT NOT NULL` column in both, `received_at TEXT NOT NULL` on `interactions`, no `uploaded_at`/`upload_error`, primary keys `(client, name)` and `(client, interaction, file)`.

- [ ] **Step 1: Create `server/pyproject.toml` and install**

`name = "vitality-server"`, `requires-python = ">=3.12"`, dependencies `fastapi`, `uvicorn`, `python-multipart`, `httpx`, `vitality-protocol` (uv path source `../protocol`, editable); script `vitality-server = "vitality_server.main:main"`; dev group `pytest`, `pytest-asyncio`, `pyright`; pyright config as in Task 1; `[tool.pytest.ini_options] asyncio_mode = "auto"`. Run `cd server && uv venv --python 3.14 && uv sync`.

- [ ] **Step 2: Write the failing tests**

```python
# server/tests/test_storage.py
import pytest
from vitality_protocol.messages import Interaction
from vitality_server.storage import ConflictingUpload, FileStore, InvalidUpload

WEBP = b"RIFF\x10\x00\x00\x00WEBPVP8 " + b"\x00" * 8
META = Interaction.model_validate({
    "name": "2026-10-25/17-00-00", "source": "screen",
    "start": "2026-10-25T17:00:00.449Z", "end": "2026-10-25T17:00:04.120Z",
    "local_start": "2026-10-25T10:00:00.449-07:00", "tz": "America/Vancouver",
    "shots": [{"file": "0000.webp", "ts": "2026-10-25T17:00:00.449Z", "trigger": "activity",
               "width": 8, "height": 8, "bytes": len(WEBP)}]})
FILES = {"0000.webp": WEBP}

def test_put_stores_folder_and_index(tmp_path):
    s = FileStore(tmp_path)
    assert s.put("laptop", META, FILES) == "new"
    folder = tmp_path / "captures/laptop/2026-10-25/17-00-00"
    assert (folder / "0000.webp").read_bytes() == WEBP
    assert Interaction.model_validate_json((folder / "interaction.json").read_text()) == META
    assert not any((tmp_path / "tmp").iterdir())

def test_identical_repeat_is_existing(tmp_path):
    s = FileStore(tmp_path); s.put("laptop", META, FILES)
    assert s.put("laptop", META, FILES) == "existing"

def test_same_interaction_different_json_formatting_is_existing(tmp_path):  # Review Focus 3
    s = FileStore(tmp_path); s.put("laptop", META, FILES)
    folder = tmp_path / "captures/laptop/2026-10-25/17-00-00"
    (folder / "interaction.json").write_text(META.model_dump_json(indent=4).replace("Z\"", "+00:00\""))
    assert s.put("laptop", META, FILES) == "existing"

def test_different_content_same_name_conflicts(tmp_path):
    s = FileStore(tmp_path); s.put("laptop", META, FILES)
    with pytest.raises(ConflictingUpload):
        s.put("laptop", META.model_copy(update={"tz": "UTC"}), FILES)

def test_suffixed_name_is_accepted(tmp_path):                               # Review Focus 2
    meta = META.model_copy(update={"name": "2026-10-25/17-00-00-2"})
    assert FileStore(tmp_path).put("laptop", meta, FILES) == "new"

@pytest.mark.parametrize("meta,files,reason", [
    (META.model_copy(update={"name": "2026-10-25/17-00-01"}), FILES, "name"),
    (META, {}, "files"),
    (META, {"0000.webp": WEBP, "0001.webp": WEBP}, "files"),
    (META, {"0000.webp": WEBP + b"x"}, "size"),
    (META, {"0000.webp": b"\x89PNG" + b"\x00" * 20}, "WebP"),   # same length as WEBP
])
def test_invalid_uploads_rejected(tmp_path, meta, files, reason):
    with pytest.raises(InvalidUpload, match=reason):
        FileStore(tmp_path).put("laptop", meta, files)
    assert not (tmp_path / "captures").exists()

def test_recover_cleans_tmp_and_indexes_unindexed_folders(tmp_path):
    s = FileStore(tmp_path); s.put("laptop", META, FILES); s.close()
    (tmp_path / "index.sqlite").unlink()
    (tmp_path / "tmp/abc").mkdir(parents=True); (tmp_path / "tmp/abc/0000.webp").write_bytes(WEBP)
    s2 = FileStore(tmp_path)
    assert s2.recover() == 1 and not (tmp_path / "tmp/abc").exists()
    assert s2.put("laptop", META, FILES) == "existing"
```

`server/tests/test_config.py`: defaults; full file loads; unknown key, `port = "x"`, `allowed_clients = "laptop"` each raise `ValueError` naming the key.

- [ ] **Step 3: Run to verify failure**

Run: `cd server && uv run pytest -q`
Expected: FAIL (`ModuleNotFoundError: vitality_server.storage`)

- [ ] **Step 4: Implement**

`put` order: validate (all checks before touching disk; reasons contain the words `name`, `files`, `size`, `WebP`) → if the final folder exists, compare `Interaction.model_validate_json(existing json) == interaction` → `existing` or `ConflictingUpload` → write files and `interaction.json` (`model_dump_json(indent=2)`) into `<data_dir>/tmp/<uuid4>/` → `os.rename` into `captures/<client>/<name>` (parents created) → insert index rows with `received_at`. Files are checked for the WebP header as bytes 0–3 `RIFF` and 8–11 `WEBP`. `disk_free_bytes` = `shutil.disk_usage(data_dir).free`. Disk-full `OSError` propagates (the app maps it).

- [ ] **Step 5: Run tests and pyright**

Run: `cd server && uv run pytest -q && uv run pyright`
Expected: all pass; `0 errors`

- [ ] **Step 6: Commit**

```bash
git add server/
git commit -m "server: config and atomic interaction storage"
```

---

### Task 9: Server identity, routes and entry point

**Files:**
- Create: `server/vitality_server/identity/__init__.py`, `identity/interface.py`, `identity/tailscale.py`, `server/vitality_server/app.py`, `server/vitality_server/main.py`
- Test: `server/tests/test_identity.py`, `server/tests/test_app.py`

**Interfaces:**
- Consumes: Task 8 store and config; Task 1 messages and routes.
- Produces:

```python
# identity/interface.py
class Identity(Protocol):
    async def who(self, ip: str) -> str | None: ...       # Tailscale device name, or None

# identity/tailscale.py
Runner = Callable[[Sequence[str]], Awaitable[tuple[int, bytes]]]   # (exit code, stdout)
class TailscaleIdentity(Identity):
    def __init__(self, run: Runner = run_command) -> None: ...
async def tailscale_ipv4(run: Runner = run_command) -> str: ...      # `tailscale ip -4`, first line

# app.py
def create_app(store: InteractionStore, identity: Identity, allowed_clients: Collection[str]) -> FastAPI: ...
#   POST INTERACTIONS_PATH:
#   async def post_interaction(request: Request, response: Response,
#       interaction: Annotated[Json[Interaction], Form()],
#       shots: Annotated[list[UploadFile], File()]) -> UploadReply
#   GET HEALTH_PATH -> Health
```

  `who` runs `["tailscale", "whois", "--json", ip]` and returns `Node.ComputedName`, else the first label of `Node.Name`; `None` on non-zero exit or unparseable output. `main()` = `vitality-server [--config PATH]` (default `/etc/vitality/server.toml`): load config, `FileStore(data_dir).recover()`, `tailscale_ipv4()`, `uvicorn.run(create_app(...), host=ip, port=config.port)`.

- [ ] **Step 1: Write the failing tests**

```python
# server/tests/test_app.py
from fastapi.testclient import TestClient
from vitality_server.app import create_app
from vitality_server.storage import FileStore
from test_storage import META, WEBP          # reuse fixtures

class FakeIdentity:
    def __init__(self, name): self.name = name
    async def who(self, ip): return self.name

def client(tmp_path, device="laptop"):
    return TestClient(create_app(FileStore(tmp_path), FakeIdentity(device), ["laptop"]))

def post(c, meta=META, files=(("0000.webp", WEBP),)):
    return c.post("/interactions", data={"interaction": meta.model_dump_json()},
                  files=[("shots", (n, b, "image/webp")) for n, b in files])

def test_new_then_existing(tmp_path):
    c = client(tmp_path)
    r = post(c); assert r.status_code == 201 and r.json() == {"ok": True, "stored": "new"}
    r = post(c); assert r.status_code == 200 and r.json()["stored"] == "existing"
    assert (tmp_path / "captures/laptop/2026-10-25/17-00-00/0000.webp").exists()

def test_device_not_allowed(tmp_path):
    r = post(client(tmp_path, device="doer")); assert r.status_code == 403
    assert r.json() == {"ok": False, "error": "device not allowed"}

def test_unknown_device(tmp_path):
    assert post(client(tmp_path, device=None)).status_code == 403

def test_invalid_json_is_422_error_reply(tmp_path):
    r = client(tmp_path).post("/interactions", data={"interaction": "{}"},
                              files=[("shots", ("0000.webp", WEBP, "image/webp"))])
    assert r.status_code == 422 and r.json()["ok"] is False

def test_storage_rejection_is_422(tmp_path):
    r = post(client(tmp_path), files=(("0001.webp", WEBP),))
    assert r.status_code == 422 and "files" in r.json()["error"]

def test_conflict_is_409(tmp_path):
    c = client(tmp_path); post(c)
    assert post(c, META.model_copy(update={"tz": "UTC"})).status_code == 409

def test_too_large_is_413(tmp_path, monkeypatch):
    monkeypatch.setattr("vitality_server.app.MAX_UPLOAD_BYTES", 10)
    assert post(client(tmp_path)).status_code == 413

def test_disk_full_is_507(tmp_path, monkeypatch):
    store = FileStore(tmp_path)
    monkeypatch.setattr(store, "put", lambda *a: (_ for _ in ()).throw(OSError(28, "No space left")))
    c = TestClient(create_app(store, FakeIdentity("laptop"), ["laptop"]))
    assert post(c).status_code == 507

def test_health(tmp_path):
    r = client(tmp_path).get("/health")
    assert r.status_code == 200 and r.json()["ok"] is True and r.json()["disk_free_bytes"] > 0
```

```python
# server/tests/test_identity.py
import json
from vitality_server.identity import TailscaleIdentity

def runner(code, payload):
    async def run(argv): return code, json.dumps(payload).encode()
    return run

async def test_who_returns_computed_name():
    ident = TailscaleIdentity(runner(0, {"Node": {"ComputedName": "laptop", "Name": "laptop.tail1.ts.net."}}))
    assert await ident.who("100.64.0.2") == "laptop"

async def test_who_falls_back_to_name_label():
    assert await TailscaleIdentity(runner(0, {"Node": {"Name": "laptop.tail1.ts.net."}})).who("x") == "laptop"

async def test_who_unknown_is_none():
    assert await TailscaleIdentity(runner(1, {})).who("1.2.3.4") is None
```

- [ ] **Step 2: Run to verify failure**

Run: `cd server && uv run pytest -q`
Expected: FAIL (`ModuleNotFoundError: vitality_server.app`)

- [ ] **Step 3: Implement**

`create_app` registers: a middleware rejecting requests whose `Content-Length` exceeds the module-level `MAX_UPLOAD_BYTES` (read at request time, so the test can lower it) (`413`) or is missing on `POST` (`411`), both as `ErrorReply`; an exception handler turning `RequestValidationError` into `422` `ErrorReply(error=<first error message>)`; the two routes. `post_interaction` checks `await identity.who(request.client.host)` is in `allowed_clients` (else `403 "device not allowed"`), reads the files, calls `store.put` via `run_in_threadpool`, maps `InvalidUpload` → 422, `ConflictingUpload` → 409 (logged at error), `OSError` with `errno.ENOSPC` → 507; sets `201` for `new`. Logs one info line per upload: device, interaction name, result. The module docstring is the route table.

- [ ] **Step 4: Run tests and pyright**

Run: `cd server && uv run pytest -q && uv run pyright`
Expected: all pass; `0 errors`

- [ ] **Step 5: Commit**

```bash
git add server/
git commit -m "server: FastAPI routes with Tailscale identity"
```

---

### Task 10: Server packaging and docs

**Files:**
- Create: `server/systemd/vitality-server.service`, `server/install.sh`, `server/README.md`, `server/server.toml.example`
- Modify: `client/README.md` (layout, upload setup, Tailscale), `docs/superpowers/specs/2026-10-21-daemon-design.md` (§4 actions, §6 storage → interaction folders and two tables, §7 status `upload`, §11 layout `upload/`; note "updated by 2026-10-25-server-ingest-design.md")
- Test: `bash -n server/install.sh`; `systemd-analyze verify server/systemd/vitality-server.service` (on the laptop)

**Interfaces:**
- Produces: `install.sh` (run from the repo root on the VM, with sudo available): installs `uv` if missing; `uv python install 3.14`; `uv venv --python 3.14 /opt/vitality-server`; `uv pip install --python /opt/vitality-server/bin/python ./protocol ./server`; creates system user `vitality` (no login); creates `/srv/vitality` owned by `vitality`; writes `/etc/vitality/server.toml` from the example only if absent; installs the unit to `/etc/systemd/system/`, `daemon-reload`, `enable --now`. Rerunning upgrades the code and restarts the service.
- Unit: `After=network-online.target tailscaled.service`, `Wants=tailscaled.service`, `User=vitality`, `ExecStart=/opt/vitality-server/bin/vitality-server`, `Restart=on-failure`, `RestartSec=5`, `NoNewPrivileges=yes`, `ProtectSystem=strict`, `ReadWritePaths=/srv/vitality`, `WantedBy=multi-user.target`.

- [ ] **Step 1: Write the unit, example config, install script and server README**

The README covers: what the server does; Tailscale setup on the VM (`sudo tailscale up`, `sudo tailscale set --hostname=vitality-librarian`); copying the repo (`rsync -a --exclude .venv --exclude .git ./ vitality-librarian-vm:Vitality/`); running `install.sh`; editing `allowed_clients`; checking `systemctl status vitality-server`, `journalctl -u vitality-server`, `curl http://vitality-librarian:8100/health`; data layout under `/srv/vitality`.

- [ ] **Step 2: Verify**

Run: `bash -n server/install.sh && systemd-analyze verify server/systemd/vitality-server.service`
Expected: no syntax errors; `systemd-analyze` reports nothing other than the missing ExecStart binary on the laptop.

- [ ] **Step 3: Update the client README and daemon spec**

Client README: data layout section (interaction folders, `interaction.json`), `upload_server` in `client.toml`, Tailscale install on Arch (`sudo pacman -S tailscale && sudo systemctl enable --now tailscaled && sudo tailscale up && sudo tailscale set --hostname=laptop`), the new `status` line and doctor checks; the top paragraph no longer says "Nothing leaves the machine".

- [ ] **Step 4: Commit**

```bash
git add server/ client/README.md docs/superpowers/specs/2026-10-21-daemon-design.md
git commit -m "server: install script, systemd unit and docs"
```

---

### Task 11: Deploy, migrate and verify (operational)

**Files:** none in the repo. The migration script lives in the session scratchpad and is never committed.

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Tailscale on both machines (needs the user)**

Laptop (user runs, needs sudo): `sudo pacman -S tailscale && sudo systemctl enable --now tailscaled && sudo tailscale up && sudo tailscale set --hostname=laptop`. VM: `sudo tailscale up` (user opens the printed link) and `sudo tailscale set --hostname=vitality-librarian`; optionally `ssh exe.dev rename commode-doubled vitality-librarian`.
Verify: `tailscale status` on the laptop lists `vitality-librarian`; `ssh vitality-librarian-vm 'tailscale whois --json <laptop tailscale ip>'` shows `ComputedName` `laptop` **as the `vitality` user** (`sudo -u vitality tailscale whois ...`), confirming the service account may query identity.

- [ ] **Step 2: Install the server**

rsync the repo to the VM, run `server/install.sh`, set `allowed_clients = ["laptop"]`, restart.
Verify from the laptop: `curl -s http://vitality-librarian:8100/health` → `{"ok":true,...}`; `curl -s --max-time 5 https://<vm>.exe.xyz/health` does not reach the server.

- [ ] **Step 3: Migrate the laptop's captures**

Stop the service (`systemctl --user stop vitality-client`); back up `~/.local/share/vitality/client` to `client.bak`. Write and run the scratchpad script with the client venv's Python. Per old `interaction_id`, in `ts` order: `start` = the `before` capture's ts, or (no before) the first capture's ts minus `settle_seconds` (2 s); name via `interaction_name(start, taken)`; each capture → `shot_filename(int(ts - start), taken)`, `trigger = "activity"`; `os.rename` the file; insert rows through the new `Store`; `end_interaction`; write `interaction.json`. Then drop the old `captures` table. The script skips interactions whose rows already exist (rerunnable).
Verify: new shot count = old capture count; new interaction count = old distinct `interaction_id` count; `find captures -name '*.webp' | wc -l` matches; three folders spot-checked; no files left in old `captures/YYYY-MM-DD/` top level.

- [ ] **Step 4: Start the new daemon with upload**

`cd client && uv sync`; add `upload_server = "http://vitality-librarian:8100"` to `~/.config/vitality/client.toml`; `systemctl --user start vitality-client`.
Verify: `vitality-client status` shows `upload: ok`, pending falls to 0 over a few minutes; on the VM, `sqlite3 /srv/vitality/index.sqlite 'select count(*) from interactions'` equals the laptop's interaction count; `vitality-client doctor` all ✓.

- [ ] **Step 5: Freshness and offline behaviour**

Use the laptop for a minute; within about a minute of each interaction ending it appears under `/srv/vitality/captures/laptop/`. Then `ssh` to the VM and `sudo systemctl stop vitality-server`, keep working for a minute (status shows `offline` and growing pending), start it again: pending drains to 0.

- [ ] **Step 6: Clean up**

After the user confirms laptop and server agree: delete `~/.local/share/vitality/client.bak`; remove `tmp-framework-comparison.md` from the repo root if the user is done with it.
