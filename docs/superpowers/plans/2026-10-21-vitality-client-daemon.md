# vitality-client Daemon Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Wayland-only background service that saves before/after screenshots of the whole desktop around keyboard/mouse activity as WebP files plus a SQLite index, controllable via a `vitality-client` CLI.

**Architecture:** One asyncio Python process. An evdev input watcher emits bare timestamps to a pure capture policy; the policy emits snapshot requests; a writer grabs the latest frame from a portal/PipeWire/GStreamer frame source, writes WebP then an index row. A Unix-socket control server serves pause/resume/status for the CLI and persists pause state.

**Tech Stack:** Python ≥ 3.12 (system 3.14 on the target), uv, pytest, pytest-asyncio, python-evdev, dbus-fast, PyGObject + GStreamer (system packages), Pillow, python-ulid.

**Spec:** `docs/superpowers/specs/2026-10-21-daemon-design.md`

## Global Constraints

- All code lives in `client/`; package `vitality_client`; console script `vitality-client`.
- Python ≥ 3.12. The venv is created with system site packages so the system PyGObject/GStreamer are importable: `uv venv --python /usr/bin/python3 --system-site-packages` (run in `client/`).
- Wayland only: if `XDG_SESSION_TYPE != "wayland"`, `run` and `capture-once` exit with code 1 and the message `vitality-client requires a Wayland session (XDG_SESSION_TYPE=<value>)`.
- Paths (env var if set, else default):
  - config: `$XDG_CONFIG_HOME/vitality/client.toml` (default `~/.config/vitality/client.toml`)
  - data: `$XDG_DATA_HOME/vitality/client/` (default `~/.local/share/vitality/client/`)
  - state: `$XDG_STATE_HOME/vitality/client/state.json` (default `~/.local/state/vitality/client/state.json`)
  - socket: `$XDG_RUNTIME_DIR/vitality/client.sock`
- Config defaults: `settle_seconds = 2`, `max_gap_seconds = 10`, `webp_quality = 85`, `disk_warning_gb = 20`.
- Images: whole desktop, native resolution, lossy WebP at `webp_quality`; scaled down only if a side exceeds 16383 px.
- Captures stored at `captures/YYYY-MM-DD/<capture_id>.webp` (UTC date) relative to the data dir; index at `index.sqlite`.
- `source` is always `"screen"`; `kind` is `"before"` or `"after"`; ids are ULIDs (`str(ulid.ULID())`).
- `ts` stored as UTC ISO 8601 with milliseconds and `Z`, e.g. `2026-10-21T14:02:05.312Z`.
- No key codes, pointer coordinates, or image contents may appear in logs, return values of the activity module, or the control protocol.
- Times inside the daemon are wall-clock epoch seconds (`time.time()`), because pause deadlines persist across restarts.
- Dependency note: the spec names `dbus-next`; this plan uses **dbus-fast**, its maintained fork with the same API, because dbus-next is unmaintained.

## Review Focus

1. **No frame available yet** (portal not started, first frame not arrived, or capture lost) when a snapshot is requested → request is dropped with a log line, no file or row, daemon keeps running. Pinned in Task 8.
2. **Wall clock jumps** (suspend/resume, NTP moving time backwards) → policy never emits duplicate or negative-gap snapshots and never gets stuck; a forward jump after suspend simply closes the open interaction with one `after`. Pinned in Task 2.
3. **Input device unplugged mid-read** (USB mouse, Bluetooth keyboard sleeping) → that device is dropped, others keep working, it is picked up again on reconnect. Pinned in Task 5.
4. **Stale socket / daemon not running** → `run` starts even if a stale `client.sock` exists from a crash; CLI commands print `vitality-client daemon is not running` and exit 1 instead of a traceback. Pinned in Task 6.
5. **Write failure** (disk full, permission) during a snapshot → logged, no row inserted, no temp file left, daemon continues. Pinned in Tasks 4 and 8.

---

## File Structure

```
client/
  pyproject.toml                  # project, deps, console script, pytest config
  README.md                       # setup and usage
  systemd/vitality-client.service
  vitality_client/
    __init__.py
    config.py        # Config, Paths, load_config, resolve_paths
    policy.py        # CapturePolicy, SnapshotRequest (pure)
    store.py         # Store: SQLite schema + queries, CaptureRow
    writer.py        # Writer: WebP encode, atomic file write, index row, orphan cleanup
    activity.py      # ActivityWatcher (evdev), is_activity_device
    control.py       # StateFile, parse_duration, ControlServer, send_command
    frames/
      __init__.py
      base.py        # FrameSource protocol, composite()
      portal.py      # PortalFrameSource (portal + PipeWire + GStreamer)
    daemon.py        # Daemon: wires units, main loop, status
    doctor.py        # setup checks
    cli.py           # vitality-client entry point
  tests/
    test_config.py  test_policy.py  test_store.py  test_writer.py
    test_activity.py  test_control.py  test_frames.py  test_daemon.py
    test_doctor.py  test_cli.py
```

---

### Task 1: Project scaffold and configuration

**Files:**
- Create: `client/pyproject.toml`, `client/vitality_client/__init__.py`, `client/vitality_client/config.py`, `client/tests/test_config.py`, `client/.gitignore` (`.venv/`, `__pycache__/`)

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class Config: settle_seconds: float = 2; max_gap_seconds: float = 10; webp_quality: int = 85; disk_warning_gb: float = 20`
  - `@dataclass(frozen=True) class Paths: config_file: Path; data_dir: Path; state_file: Path; socket_path: Path`
  - `resolve_paths(env: Mapping[str, str], home: Path) -> Paths`
  - `load_config(path: Path) -> Config` — missing file → defaults; unknown keys → `ValueError` naming the key; wrong type → `ValueError`.

- [ ] **Step 1: Create `pyproject.toml`**

  Name `vitality-client`, `requires-python = ">=3.12"`, dependencies `evdev`, `dbus-fast`, `Pillow`, `python-ulid`; dev group `pytest`, `pytest-asyncio`; `[project.scripts] vitality-client = "vitality_client.cli:main"`; `[tool.pytest.ini_options] asyncio_mode = "auto"`. Then in `client/`: `uv venv --python /usr/bin/python3 --system-site-packages && uv sync`.

- [ ] **Step 2: Write failing tests** in `tests/test_config.py`

```python
def test_resolve_paths_defaults(tmp_path):
    p = resolve_paths({"XDG_RUNTIME_DIR": "/run/user/1000"}, home=tmp_path)
    assert p.config_file == tmp_path / ".config/vitality/client.toml"
    assert p.data_dir == tmp_path / ".local/share/vitality/client"
    assert p.state_file == tmp_path / ".local/state/vitality/client/state.json"
    assert p.socket_path == Path("/run/user/1000/vitality/client.sock")

def test_resolve_paths_respects_xdg_vars(tmp_path):
    env = {"XDG_CONFIG_HOME": "/c", "XDG_DATA_HOME": "/d", "XDG_STATE_HOME": "/s", "XDG_RUNTIME_DIR": "/r"}
    p = resolve_paths(env, home=tmp_path)
    assert p.config_file == Path("/c/vitality/client.toml")
    assert p.data_dir == Path("/d/vitality/client")
    assert p.state_file == Path("/s/vitality/client/state.json")
    assert p.socket_path == Path("/r/vitality/client.sock")

def test_load_config_missing_file_gives_defaults(tmp_path):
    assert load_config(tmp_path / "nope.toml") == Config()

def test_load_config_overrides(tmp_path):
    f = tmp_path / "client.toml"; f.write_text("settle_seconds = 3\nwebp_quality = 90\n")
    c = load_config(f)
    assert (c.settle_seconds, c.webp_quality, c.max_gap_seconds) == (3, 90, 10)

def test_load_config_unknown_key_rejected(tmp_path):
    f = tmp_path / "client.toml"; f.write_text("settle = 3\n")
    with pytest.raises(ValueError, match="settle"):
        load_config(f)
```

  Missing `XDG_RUNTIME_DIR` → `resolve_paths` raises `RuntimeError("XDG_RUNTIME_DIR is not set")`; add that test too.

- [ ] **Step 3: Run** `uv run pytest tests/test_config.py -v` — Expected: FAIL (import error)
- [ ] **Step 4: Implement `config.py`** using `tomllib`.
- [ ] **Step 5: Run** `uv run pytest tests/test_config.py -v` — Expected: PASS
- [ ] **Step 6: Commit** `git add client && git commit -m "client: scaffold project and config"`

---

### Task 2: Capture policy

**Files:**
- Create: `client/vitality_client/policy.py`, `client/tests/test_policy.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class SnapshotRequest: kind: Literal["before", "after"]; interaction_id: str; ts: float`
  - `class CapturePolicy(settle_seconds: float, max_gap_seconds: float, new_id: Callable[[], str], paused: bool = False, paused_until: float | None = None)`
    - `on_activity(now: float) -> list[SnapshotRequest]`
    - `on_tick(now: float) -> list[SnapshotRequest]`
    - `pause(now: float, until: float | None = None) -> list[SnapshotRequest]`
    - `resume() -> None`
    - `next_deadline() -> float | None` — earliest time `on_tick` has work (settle, max gap, or timed resume)
    - properties `state: Literal["idle", "active", "paused"]`, `paused_until: float | None`

Rules (spec §4): idle+activity → `before`, new id, active. Active+activity → reset settle. Every call first processes due deadlines: settle expired → `after`, idle; `now - last_snapshot >= max_gap` while active → `after`, same id. `pause` while active → final `after`, then paused. Timed pause expiry → idle. A paused policy constructed with `paused_until <= now` resumes on its first call. If `now` is earlier than the last snapshot time (clock moved backwards), no deadline fires and no request is emitted for that call except the idle→`before` transition.

- [ ] **Step 1: Write failing tests** (ids from a counter `new_id = iter(["i1","i2","i3"]).__next__`; `P = lambda **kw: CapturePolicy(2, 10, new_id, **kw)`)

```python
def test_idle_activity_emits_before():
    p = P(); assert p.on_activity(100.0) == [SnapshotRequest("before", "i1", 100.0)]; assert p.state == "active"

def test_settle_emits_after_then_idle():
    p = P(); p.on_activity(100.0); p.on_activity(101.0)
    assert p.on_tick(102.9) == []
    assert p.on_tick(103.0) == [SnapshotRequest("after", "i1", 103.0)]
    assert p.state == "idle"

def test_continuous_activity_forces_after_every_max_gap():
    p = P(); out = []
    for t in range(0, 25): out += p.on_activity(100.0 + t)      # one event per second, never settles
    assert [(r.kind, r.ts) for r in out] == [("before", 100.0), ("after", 110.0), ("after", 120.0)]
    assert {r.interaction_id for r in out} == {"i1"}

def test_next_deadline_tracks_settle_and_gap():
    p = P(); p.on_activity(100.0); assert p.next_deadline() == 102.0
    assert P().next_deadline() is None

def test_pause_while_active_emits_final_after_and_ignores_activity():
    p = P(); p.on_activity(100.0)
    assert p.pause(101.0) == [SnapshotRequest("after", "i1", 101.0)]
    assert p.on_activity(102.0) == [] and p.on_tick(200.0) == [] and p.state == "paused"

def test_pause_while_idle_emits_nothing():
    assert P().pause(100.0) == []

def test_timed_pause_resumes():
    p = P(); p.pause(100.0, until=160.0)
    assert p.next_deadline() == 160.0
    p.on_tick(160.0); assert p.state == "idle"

def test_expired_timed_pause_from_previous_run_resumes():
    p = P(paused=True, paused_until=50.0); p.on_tick(100.0); assert p.state == "idle"

def test_untimed_pause_from_previous_run_stays_paused():
    p = P(paused=True); assert p.on_activity(100.0) == [] and p.state == "paused"

def test_resume_returns_to_idle():
    p = P(); p.pause(100.0); p.resume(); assert p.state == "idle"

def test_clock_backwards_does_not_emit_or_stick():          # Review Focus 2
    p = P(); p.on_activity(100.0)
    assert p.on_tick(50.0) == []
    assert p.on_tick(102.0) == [SnapshotRequest("after", "i1", 102.0)]

def test_forward_jump_after_suspend_closes_interaction_once():   # Review Focus 2
    p = P(); p.on_activity(100.0)
    assert [r.kind for r in p.on_tick(5000.0)] == ["after"]
    assert p.on_tick(5001.0) == []
```

- [ ] **Step 2: Run** `uv run pytest tests/test_policy.py -v` — Expected: FAIL
- [ ] **Step 3: Implement `policy.py`.** When both settle and max-gap are due in one call, emit a single `after` (settle wins, go idle).
- [ ] **Step 4: Run** `uv run pytest tests/test_policy.py -v` — Expected: PASS
- [ ] **Step 5: Commit** `git commit -m "client: capture policy"`

---

### Task 3: Index store

**Files:**
- Create: `client/vitality_client/store.py`, `client/tests/test_store.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class CaptureRow: capture_id: str; interaction_id: str; kind: str; source: str; ts: str; path: str; width: int; height: int; bytes: int` (`uploaded_at` not in the dataclass; always NULL on insert)
  - `class Store(db_path: Path)` — creates parent dir and schema on open; WAL mode.
    - `insert(row: CaptureRow) -> None`
    - `paths() -> set[str]` — all `path` values
    - `count_since(ts: str) -> int` — rows with `ts >= ts`
    - `last_ts() -> str | None`
    - `close() -> None`

Schema: table `captures` with columns exactly as spec §6 (`capture_id TEXT PRIMARY KEY, interaction_id TEXT NOT NULL, kind TEXT NOT NULL CHECK (kind IN ('before','after')), source TEXT NOT NULL, ts TEXT NOT NULL, path TEXT NOT NULL, width INTEGER NOT NULL, height INTEGER NOT NULL, bytes INTEGER NOT NULL, uploaded_at TEXT`), indexes `idx_captures_ts` on `ts`, `idx_captures_uploaded_at` on `uploaded_at`.

- [ ] **Step 1: Write failing tests**

```python
ROW = CaptureRow("01J...A", "01J...I", "before", "screen", "2026-10-21T14:02:05.312Z",
                 "captures/2026-10-21/01J...A.webp", 2560, 1600, 123456)

def test_insert_and_query(tmp_path):
    s = Store(tmp_path / "index.sqlite"); s.insert(ROW)
    assert s.paths() == {ROW.path}
    assert s.count_since("2026-10-21T00:00:00.000Z") == 1
    assert s.count_since("2026-10-22T00:00:00.000Z") == 0
    assert s.last_ts() == ROW.ts

def test_uploaded_at_is_null(tmp_path):
    s = Store(tmp_path / "index.sqlite"); s.insert(ROW)
    assert sqlite3.connect(tmp_path / "index.sqlite").execute("select uploaded_at from captures").fetchone() == (None,)

def test_schema_persists_across_reopen(tmp_path):
    Store(tmp_path / "index.sqlite").insert(ROW)
    assert Store(tmp_path / "index.sqlite").last_ts() == ROW.ts

def test_invalid_kind_rejected(tmp_path):
    with pytest.raises(sqlite3.IntegrityError):
        Store(tmp_path / "i.sqlite").insert(dataclasses.replace(ROW, kind="during"))
```

- [ ] **Step 2: Run** `uv run pytest tests/test_store.py -v` — Expected: FAIL
- [ ] **Step 3: Implement `store.py`** with the stdlib `sqlite3`.
- [ ] **Step 4: Run** `uv run pytest tests/test_store.py -v` — Expected: PASS
- [ ] **Step 5: Commit** `git commit -m "client: SQLite capture index"`

---

### Task 4: Writer

**Files:**
- Create: `client/vitality_client/writer.py`, `client/tests/test_writer.py`

**Interfaces:**
- Consumes: `Store`, `CaptureRow` (Task 3); `SnapshotRequest` (Task 2)
- Produces:
  - `class Writer(data_dir: Path, store: Store, quality: int, new_id: Callable[[], str])`
    - `write(image: PIL.Image.Image, req: SnapshotRequest) -> CaptureRow` — fits image within 16383 px per side (aspect preserved), encodes WebP to `<final>.tmp`, `os.replace` to final path, then `store.insert`. On any exception: remove the temp file if present, re-raise.
    - `cleanup_orphans() -> int` — deletes `captures/**/*.webp` not in `store.paths()` and any `*.tmp`; returns count deleted.
    - `disk_usage_bytes() -> int` — total size of `captures/`.
  - `format_ts(epoch: float) -> str` → `"2026-10-21T14:02:05.312Z"`

- [ ] **Step 1: Write failing tests** (fixture `img = Image.new("RGB", (64, 40), "white")`)

```python
def test_write_creates_file_and_row(tmp_path):
    store = Store(tmp_path / "index.sqlite"); w = Writer(tmp_path, store, 85, lambda: "CAP1")
    row = w.write(img, SnapshotRequest("before", "INT1", 1761055325.312))
    assert row.path == "captures/2025-10-21/CAP1.webp"     # UTC date of the ts
    assert (row.kind, row.interaction_id, row.source, row.ts) == ("before", "INT1", "screen", "2025-10-21T14:02:05.312Z")
    f = tmp_path / row.path
    assert f.exists() and Image.open(f).format == "WEBP" and Image.open(f).size == (64, 40)
    assert row.bytes == f.stat().st_size and store.paths() == {row.path}

def test_oversized_image_scaled_to_fit(tmp_path):
    w = Writer(tmp_path, Store(tmp_path / "i.sqlite"), 85, lambda: "C")
    row = w.write(Image.new("RGB", (20000, 1000)), SnapshotRequest("after", "I", 0.0))
    assert row.width == 16383 and row.height == 819

def test_failed_insert_leaves_no_row_and_cleanup_removes_file(tmp_path):     # Review Focus 5
    store = Store(tmp_path / "i.sqlite"); w = Writer(tmp_path, store, 85, lambda: "C")
    with patch.object(store, "insert", side_effect=sqlite3.OperationalError("disk full")):
        with pytest.raises(sqlite3.OperationalError): w.write(img, SnapshotRequest("after", "I", 0.0))
    assert not list(tmp_path.rglob("*.tmp"))
    assert w.cleanup_orphans() == 1 and not list(tmp_path.rglob("*.webp"))

def test_failed_encode_leaves_no_temp_file(tmp_path):                        # Review Focus 5
    w = Writer(tmp_path, Store(tmp_path / "i.sqlite"), 85, lambda: "C")
    with patch("PIL.Image.Image.save", side_effect=OSError("no space")):
        with pytest.raises(OSError): w.write(img, SnapshotRequest("after", "I", 0.0))
    assert not list(tmp_path.rglob("*.tmp")) and not list(tmp_path.rglob("*.webp"))

def test_disk_usage(tmp_path):
    w = Writer(tmp_path, Store(tmp_path / "i.sqlite"), 85, lambda: "C")
    row = w.write(img, SnapshotRequest("after", "I", 0.0)); assert w.disk_usage_bytes() == row.bytes
```

- [ ] **Step 2: Run** `uv run pytest tests/test_writer.py -v` — Expected: FAIL
- [ ] **Step 3: Implement `writer.py`** (Pillow `image.save(tmp, "WEBP", quality=quality)`; scaling with `Image.thumbnail((16383, 16383))` only when needed).
- [ ] **Step 4: Run** `uv run pytest tests/test_writer.py -v` — Expected: PASS
- [ ] **Step 5: Commit** `git commit -m "client: WebP writer with crash-safe ordering"`

---

### Task 5: Activity watcher

**Files:**
- Create: `client/vitality_client/activity.py`, `client/tests/test_activity.py`

**Interfaces:**
- Produces:
  - `is_activity_device(capabilities: dict[int, list]) -> bool` — True if the device has `EV_KEY` with any of `KEY_A`…`KEY_Z`/`KEY_SPACE` (keyboard) or `BTN_LEFT`/`BTN_TOUCH` (mouse, touchpad), or has `EV_REL` with `REL_X`. Power buttons, lid switches, headphone jacks → False.
  - `class ActivityWatcher(on_activity: Callable[[float], None], list_devices: Callable[[], list[str]] = evdev.list_devices, open_device: Callable[[str], Any] = evdev.InputDevice, clock: Callable[[], float] = time.time, rescan_seconds: float = 5)`
    - `async run() -> None` — every `rescan_seconds`, opens devices not yet watched that pass `is_activity_device`; one reader task per device iterates `device.async_read_loop()` and, for each event with type `EV_KEY`, `EV_REL`, or `EV_ABS`, calls `on_activity(clock())`. The event object is never passed on or stored. `OSError` from a device → close and forget it (re-opened on a later rescan).
    - `available: bool` — True once at least one device is open; False if every candidate raised `PermissionError`.

- [ ] **Step 1: Write failing tests** with a `FakeDevice` (`capabilities()` returns a dict; `async_read_loop()` yields fake events with `.type`, `.code`, `.value`; optional `raise_after=n` raises `OSError` after n events)

```python
async def test_events_reported_as_timestamp_only():
    seen = []
    dev = FakeDevice(KEYBOARD_CAPS, events=[ev(EV_KEY, KEY_A, 1), ev(EV_KEY, KEY_A, 0)])
    w = ActivityWatcher(seen.append, lambda: ["/dev/input/event3"], lambda p: dev, clock=lambda: 42.0)
    await run_briefly(w)
    assert seen == [42.0, 42.0] and all(type(x) is float for x in seen)

async def test_sync_events_ignored():
    seen = []; dev = FakeDevice(KEYBOARD_CAPS, events=[ev(EV_SYN, 0, 0)])
    await run_briefly(ActivityWatcher(seen.append, lambda: ["/e"], lambda p: dev))
    assert seen == []

def test_device_classification():
    assert is_activity_device(KEYBOARD_CAPS) and is_activity_device(MOUSE_CAPS) and is_activity_device(TOUCHPAD_CAPS)
    assert not is_activity_device(POWER_BUTTON_CAPS)

async def test_permission_denied_marks_unavailable():
    def deny(p): raise PermissionError(p)
    w = ActivityWatcher(lambda t: None, lambda: ["/e"], deny); await run_briefly(w)
    assert w.available is False

async def test_unplugged_device_dropped_and_reopened():          # Review Focus 3
    seen = []; devs = {"/a": FakeDevice(MOUSE_CAPS, events=[ev(EV_REL, REL_X, 3)], raise_after=1),
                       "/b": FakeDevice(KEYBOARD_CAPS, events=[ev(EV_KEY, KEY_A, 1)])}
    opens = []
    def open_(p): opens.append(p); return devs[p]
    w = ActivityWatcher(seen.append, lambda: list(devs), open_, clock=lambda: 1.0, rescan_seconds=0.01)
    await run_briefly(w, seconds=0.1)
    assert seen.count(1.0) >= 2 and opens.count("/a") >= 2     # /b kept working; /a re-opened on rescan
```

  `run_briefly(w, seconds=0.05)` runs `w.run()` as a task, sleeps, cancels it.

- [ ] **Step 2: Run** `uv run pytest tests/test_activity.py -v` — Expected: FAIL
- [ ] **Step 3: Implement `activity.py`.** Log device names only, never events.
- [ ] **Step 4: Run** `uv run pytest tests/test_activity.py -v` — Expected: PASS
- [ ] **Step 5: Commit** `git commit -m "client: evdev activity watcher (timestamps only)"`

---

### Task 6: Control — state file, durations, socket server/client

**Files:**
- Create: `client/vitality_client/control.py`, `client/tests/test_control.py`

**Interfaces:**
- Produces:
  - `@dataclass class State: paused: bool = False; paused_until: float | None = None; restore_token: str | None = None`
  - `class StateFile(path: Path)`: `load() -> State` (missing or corrupt file → `State()` with a warning log), `save(state: State) -> None` (write temp + `os.replace`, create parent dir)
  - `parse_duration(text: str) -> float` — accepts `<int>s|m|h` (`"30m"` → 1800.0, `"2h"` → 7200.0, `"45s"` → 45.0); anything else → `ValueError("invalid duration: <text>")`
  - `class ControlServer(socket_path: Path, handler: Callable[[dict], Awaitable[dict]])`: `async start()`, `async close()`. Protocol: one JSON object per line in each direction, one request per connection. Creates the parent dir (mode 0700). If the socket path exists: try connecting — if it answers, raise `RuntimeError("vitality-client daemon is already running")`; otherwise unlink the stale file and bind. Handler exceptions → `{"ok": false, "error": "<message>"}`.
  - `async send_command(socket_path: Path, request: dict) -> dict` — raises `DaemonNotRunning` (subclass of `RuntimeError`, message `vitality-client daemon is not running`) when the socket is missing or refuses.
  - Requests used by later tasks: `{"cmd": "pause", "seconds": float | None}`, `{"cmd": "resume"}`, `{"cmd": "status"}`.

- [ ] **Step 1: Write failing tests**

```python
@pytest.mark.parametrize("text,secs", [("30m", 1800.0), ("2h", 7200.0), ("45s", 45.0)])
def test_parse_duration(text, secs): assert parse_duration(text) == secs

@pytest.mark.parametrize("bad", ["", "30", "m", "-5m", "1.5h", "3d"])
def test_parse_duration_rejects(bad):
    with pytest.raises(ValueError, match="invalid duration"): parse_duration(bad)

def test_state_file_roundtrip_and_defaults(tmp_path):
    f = StateFile(tmp_path / "s/state.json"); assert f.load() == State()
    f.save(State(True, 123.0, "tok")); assert f.load() == State(True, 123.0, "tok")
    (tmp_path / "s/state.json").write_text("{not json"); assert f.load() == State()

async def test_request_response_roundtrip(tmp_path):
    async def handler(req): return {"ok": True, "echo": req["cmd"]}
    srv = ControlServer(tmp_path / "c.sock", handler); await srv.start()
    assert await send_command(tmp_path / "c.sock", {"cmd": "status"}) == {"ok": True, "echo": "status"}
    await srv.close()

async def test_stale_socket_is_replaced(tmp_path):                       # Review Focus 4
    stale = tmp_path / "c.sock"; s = socket.socket(socket.AF_UNIX); s.bind(str(stale)); s.close()
    srv = ControlServer(stale, AsyncMock(return_value={"ok": True})); await srv.start()
    assert (await send_command(stale, {"cmd": "status"}))["ok"]; await srv.close()

async def test_second_server_refuses(tmp_path):
    a = ControlServer(tmp_path / "c.sock", AsyncMock(return_value={"ok": True})); await a.start()
    with pytest.raises(RuntimeError, match="already running"):
        await ControlServer(tmp_path / "c.sock", AsyncMock()).start()
    await a.close()

async def test_daemon_not_running(tmp_path):                             # Review Focus 4
    with pytest.raises(DaemonNotRunning): await send_command(tmp_path / "missing.sock", {"cmd": "status"})

async def test_handler_error_is_reported(tmp_path):
    srv = ControlServer(tmp_path / "c.sock", AsyncMock(side_effect=ValueError("boom"))); await srv.start()
    assert await send_command(tmp_path / "c.sock", {"cmd": "x"}) == {"ok": False, "error": "boom"}
    await srv.close()
```

- [ ] **Step 2: Run** `uv run pytest tests/test_control.py -v` — Expected: FAIL
- [ ] **Step 3: Implement `control.py`** with `asyncio.start_unix_server` / `open_unix_connection`.
- [ ] **Step 4: Run** `uv run pytest tests/test_control.py -v` — Expected: PASS
- [ ] **Step 5: Commit** `git commit -m "client: control socket and persisted state"`

---

### Task 7: Frame source — interface, compositing, portal backend

**Files:**
- Create: `client/vitality_client/frames/__init__.py`, `frames/base.py`, `frames/portal.py`, `client/tests/test_frames.py`

**Interfaces:**
- Produces:
  - `class FrameSource(Protocol)`: `async start() -> None`; `latest_frame() -> PIL.Image.Image | None`; `available: bool`; `async close() -> None`
  - `composite(frames: list[tuple[PIL.Image.Image, tuple[int, int]]]) -> PIL.Image.Image` — places each frame at its `(x, y)` on a black canvas sized to the bounding box; positions may be negative (shift so the minimum is 0). One frame → returned unchanged.
  - `class PortalFrameSource(restore_token: str | None, on_restore_token: Callable[[str], None], max_fps: int = 5)` implementing `FrameSource`.

Portal session (D-Bus, `dbus-fast` session bus, `org.freedesktop.portal.Desktop` at `/org/freedesktop/portal/desktop`, interface `org.freedesktop.portal.ScreenCast`); each call returns a Request path whose `org.freedesktop.portal.Request.Response` signal carries `(response: u, results: a{sv})`, response `0` = success:
1. `CreateSession({"handle_token", "session_handle_token"})` → `results["session_handle"]`
2. `SelectSources(session, {"handle_token", "types": u 1 (MONITOR), "multiple": b true, "cursor_mode": u 2 (EMBEDDED), "persist_mode": u 2, "restore_token": s (only if we have one)})`
3. `Start(session, "", {"handle_token"})` → `results["streams"]: a(ua{sv})` (node id, props `position (ii)`, `size (ii)`), `results["restore_token"]` → call `on_restore_token`
4. `OpenPipeWireRemote(session, {})` → unix fd

Per stream, a GStreamer pipeline:
`pipewiresrc fd=<fd> path=<node_id> always-copy=true keepalive-time=1000 ! videorate max-rate=<max_fps> ! videoconvert ! video/x-raw,format=RGB ! appsink name=sink max-buffers=1 drop=true sync=false emit-signals=true`.
The `new-sample` callback (GStreamer thread) converts the buffer to a Pillow image (honouring row stride) and stores it under a `threading.Lock`. `latest_frame()` composites the stored frames with stream positions; returns `None` until every stream has a frame. Pipeline `ERROR`/`EOS` on the bus (polled from an asyncio task) or a non-zero portal response → `available = False`, tear down, retry `start()` with backoff 2 s, 4 s, … capped at 60 s. A rejected restore token (non-zero response with a token) → retry once without it.

- [ ] **Step 1: Write failing tests** for `composite`

```python
def test_composite_single_frame_unchanged():
    a = Image.new("RGB", (10, 5), "red"); assert composite([(a, (0, 0))]) is a

def test_composite_side_by_side():
    a = Image.new("RGB", (10, 5), "red"); b = Image.new("RGB", (4, 8), "blue")
    out = composite([(a, (0, 0)), (b, (10, 0))])
    assert out.size == (14, 8) and out.getpixel((0, 0)) == (255, 0, 0) and out.getpixel((12, 7)) == (0, 0, 255)
    assert out.getpixel((5, 7)) == (0, 0, 0)

def test_composite_negative_positions():
    a = Image.new("RGB", (10, 5), "red"); b = Image.new("RGB", (10, 5), "blue")
    out = composite([(a, (-10, 0)), (b, (0, 0))])
    assert out.size == (20, 5) and out.getpixel((0, 0)) == (255, 0, 0) and out.getpixel((10, 0)) == (0, 0, 255)
```

- [ ] **Step 2: Run** `uv run pytest tests/test_frames.py -v` — Expected: FAIL
- [ ] **Step 3: Implement `frames/base.py`.**
- [ ] **Step 4: Run** `uv run pytest tests/test_frames.py -v` — Expected: PASS
- [ ] **Step 5: Implement `frames/portal.py`** as described. It is verified manually in Task 10 (no automated test: it needs a live compositor).
- [ ] **Step 6: Smoke-check imports** `uv run python -c "import vitality_client.frames.portal"` — Expected: no output, exit 0.
- [ ] **Step 7: Commit** `git commit -m "client: frame source interface, compositing, portal backend"`

---

### Task 8: Daemon wiring and status

**Files:**
- Create: `client/vitality_client/daemon.py`, `client/tests/test_daemon.py`

**Interfaces:**
- Consumes: everything from Tasks 1–7.
- Produces:
  - `class Daemon(config: Config, paths: Paths, frames: FrameSource, watcher_factory: Callable[[Callable[[float], None]], ActivityWatcher], clock: Callable[[], float] = time.time)`
    - `async run() -> None` — loads `State` from `paths.state_file`, builds `CapturePolicy` from it, `Writer.cleanup_orphans()`, starts `ControlServer` at `paths.socket_path`, `frames.start()`, the watcher's `run()`, and a tick loop that sleeps until `policy.next_deadline()` (or until woken by activity/pause) and calls `on_tick`. Stops cleanly on `SIGTERM`/`SIGINT` (closes server, frames, store).
    - `handle_activity(now: float) -> None` — `policy.on_activity`, then `_snapshot` each request.
    - `async handle(request: dict) -> dict` — control handler for `pause` / `resume` / `status`. `pause` and `resume` persist `State` (keeping `restore_token`).
    - `status() -> dict` with keys: `ok` (True), `state` (`"idle" | "active" | "paused"`), `paused_until` (ISO string or null), `captures_today` (rows since local midnight, as UTC ISO), `last_capture` (ISO or null), `disk_bytes`, `disk_warning` (bool, `disk_bytes > disk_warning_gb * 1e9`), `activity` (`"available" | "unavailable"`), `capture` (`"available" | "unavailable"`), `paths` (`{"config", "data", "state"}` as strings).
  - `_snapshot(req)`: reads `frames.latest_frame()` **synchronously at the moment of the request** (so a `before` frame predates the app's redraw), then runs `writer.write` in `asyncio.to_thread`. `None` frame → log `snapshot dropped: no frame available` and skip. Exceptions from `write` → log and skip.
  - The `restore_token` callback for `PortalFrameSource` saves it into `State` (wired in Task 9's `cli.run`).

- [ ] **Step 1: Write failing tests** with `FakeFrames` (`latest_frame()` returns a configurable image or `None`, `available=True`) and a fake clock; drive `handle_activity` / `policy` ticks directly rather than real sleeps.

```python
async def test_activity_burst_writes_before_and_after(daemon_env):
    d, clock, data = daemon_env()
    d.handle_activity(100.0); clock.t = 103.0; await d.tick_once()
    rows = sqlite_rows(data / "index.sqlite")
    assert [r["kind"] for r in rows] == ["before", "after"] and rows[0]["interaction_id"] == rows[1]["interaction_id"]
    assert all((data / r["path"]).exists() for r in rows)

async def test_no_frame_drops_snapshot(daemon_env):                  # Review Focus 1
    d, clock, data = daemon_env(frame=None)
    d.handle_activity(100.0); await d.drain()
    assert sqlite_rows(data / "index.sqlite") == []

async def test_write_failure_is_logged_and_daemon_continues(daemon_env, caplog):   # Review Focus 5
    d, clock, data = daemon_env()
    with patch.object(Writer, "write", side_effect=OSError("disk full")):
        d.handle_activity(100.0); await d.drain()
    assert "disk full" in caplog.text
    clock.t = 200.0; d.handle_activity(200.0); await d.drain()
    # the overdue settle closes the first interaction, then a new one starts
    assert [r["kind"] for r in sqlite_rows(data / "index.sqlite")] == ["after", "before"]

async def test_pause_persists_and_survives_restart(daemon_env):
    d, clock, data = daemon_env()
    assert (await d.handle({"cmd": "pause", "seconds": None}))["ok"]
    d2, _, _ = daemon_env(reuse=data)
    assert d2.status()["state"] == "paused"

async def test_status_fields(daemon_env):
    d, _, _ = daemon_env()
    s = d.status()
    assert set(s) >= {"ok", "state", "paused_until", "captures_today", "last_capture", "disk_bytes",
                      "disk_warning", "activity", "capture", "paths"}
    assert set(s["paths"]) == {"config", "data", "state"}
```

  The `daemon_env` fixture builds `Paths` under `tmp_path`, a `Config()`, `FakeFrames`, a no-op watcher factory, and exposes `tick_once()` (one `on_tick` at `clock.t` plus snapshots) and `drain()` (await pending snapshot tasks) as small test-only helpers on `Daemon`.

- [ ] **Step 2: Run** `uv run pytest tests/test_daemon.py -v` — Expected: FAIL
- [ ] **Step 3: Implement `daemon.py`.**
- [ ] **Step 4: Run** `uv run pytest -v` (whole suite) — Expected: PASS
- [ ] **Step 5: Commit** `git commit -m "client: daemon wiring and status"`

---

### Task 9: CLI, doctor, service unit, README

**Files:**
- Create: `client/vitality_client/cli.py`, `client/vitality_client/doctor.py`, `client/systemd/vitality-client.service`, `client/README.md`, `client/tests/test_cli.py`, `client/tests/test_doctor.py`

**Interfaces:**
- Consumes: `resolve_paths`, `load_config`, `Daemon`, `PortalFrameSource`, `ActivityWatcher`, `send_command`, `DaemonNotRunning`, `parse_duration`, `Writer`, `Store`, `StateFile`.
- Produces:
  - `main(argv: list[str] | None = None) -> int` with subcommands `run`, `pause [DURATION]`, `resume`, `status [--json]`, `doctor`, `capture-once`.
  - `format_status(status: dict) -> str` — human-readable, first line like `capturing · 412 captures today · last 14:02:05 · 3.1 GB` (`paused` / `paused until 15:30` instead of `capturing` when paused; local times), followed by `activity: …`, `capture: …`, a disk warning line when set, and the three paths.
  - `doctor.run_checks(env: Mapping[str, str], paths: Paths) -> list[Check]` where `@dataclass class Check: name: str; ok: bool; hint: str`. Checks: `input group` (current user's groups contain `input`; hint `sudo usermod -aG input $USER, then log out and back in`), `wayland session`, `portal` (`org.freedesktop.portal.Desktop` exposes `org.freedesktop.portal.ScreenCast`), `pipewire` (`$XDG_RUNTIME_DIR/pipewire-0` exists), `gstreamer pipewire plugin` (`Gst.ElementFactory.find("pipewiresrc")`; hint `sudo pacman -S gst-plugin-pipewire` or the distro equivalent), `directories writable` (data, state, runtime). Each check is a small function taking injectable inputs.

Behaviour:
- `run`: Wayland check (Global Constraints message), then `asyncio.run(Daemon(...).run())`. Logging to stderr, `INFO` level, no event data.
- `pause`, `resume`, `status`: `send_command`; on `DaemonNotRunning` print `vitality-client daemon is not running` to stderr, exit 1. `pause` with an invalid duration prints the `ValueError` message, exit 2.
- `status --json`: print the status dict as one line of JSON (no `ok` key).
- `doctor`: print `✓`/`✗` per check with hints, then the three paths; exit 0 if all ok else 1.
- `capture-once`: Wayland check, start a `PortalFrameSource` with the saved restore token, wait up to 10 s for a first frame, write it as `kind="after"` in a fresh interaction, print the absolute file path, close. Timeout → `no frame received from the screen capture portal`, exit 1.
- `vitality-client.service`: `[Unit] Description=Vitality client daemon; PartOf=graphical-session.target; After=graphical-session.target` · `[Service] ExecStart=%h/.local/bin/vitality-client run; Restart=on-failure; RestartSec=5` · `[Install] WantedBy=graphical-session.target`.
- `README.md`: install (`sudo pacman -S gst-plugin-pipewire`; `sudo usermod -aG input $USER` + re-login; in `client/`: venv command from Global Constraints, `uv sync`, `uv tool install --editable . --python /usr/bin/python3` or link the venv script to `~/.local/bin/vitality-client`), `vitality-client doctor`, `vitality-client capture-once` (approve the dialog), enabling the service (`systemctl --user link …/vitality-client.service && systemctl --user enable --now vitality-client`), pause keybinding example for Hyprland (`bind = SUPER, F12, exec, vitality-client pause`), a status-bar example using `status --json`, and the `input` group security note from spec §9.

- [ ] **Step 1: Write failing tests**

```python
def test_status_when_daemon_not_running(tmp_path, monkeypatch, capsys):      # Review Focus 4
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    assert main(["status"]) == 1
    assert "vitality-client daemon is not running" in capsys.readouterr().err

def test_pause_invalid_duration(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    assert main(["pause", "3d"]) == 2 and "invalid duration: 3d" in capsys.readouterr().err

def test_run_refuses_non_wayland(monkeypatch, capsys):
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    assert main(["run"]) == 1
    assert "requires a Wayland session (XDG_SESSION_TYPE=x11)" in capsys.readouterr().err

def test_format_status_paused_and_warning():
    s = {"state": "paused", "paused_until": None, "captures_today": 3, "last_capture": None, "disk_bytes": 25e9,
         "disk_warning": True, "activity": "available", "capture": "available",
         "paths": {"config": "/c", "data": "/d", "state": "/s"}}
    out = format_status(s)
    assert out.splitlines()[0].startswith("paused · 3 captures today") and "disk" in out and "/d" in out

def test_doctor_reports_missing_input_group(tmp_path):
    checks = {c.name: c for c in run_checks_with(groups=["wheel"], tmp_path=tmp_path)}
    assert not checks["input group"].ok and "usermod -aG input" in checks["input group"].hint
```

  (`run_checks_with` is a test helper that calls the individual check functions with injected groups/env/paths.)

- [ ] **Step 2: Run** `uv run pytest tests/test_cli.py tests/test_doctor.py -v` — Expected: FAIL
- [ ] **Step 3: Implement `doctor.py`, `cli.py`, the service unit, and `README.md`.**
- [ ] **Step 4: Run** `uv run pytest -v` — Expected: PASS
- [ ] **Step 5: Commit** `git commit -m "client: CLI, doctor, systemd unit, README"`

---

### Task 10: Manual end-to-end verification on the target laptop

Needs the author at the machine (permission dialog, group change, re-login).

- [ ] **Step 1:** Follow the README install steps. Run `vitality-client doctor` — Expected: all checks `✓`.
- [ ] **Step 2:** Run `vitality-client capture-once`, approve the portal dialog — Expected: prints a path; the WebP opens and shows the whole desktop with the cursor; a row exists (`sqlite3 ~/.local/share/vitality/client/index.sqlite 'select kind, ts, width, height from captures'`).
- [ ] **Step 3:** Run `vitality-client capture-once` again — Expected: no dialog this time (restore token reused).
- [ ] **Step 4:** Start `vitality-client run` in a terminal. Type a sentence, wait 3 s — Expected: one `before` and one `after` row with the same `interaction_id`; the `before` image predates the typed text, the `after` shows it.
- [ ] **Step 5:** Scroll continuously for ~25 s — Expected: `before`, then `after` roughly every 10 s, then a final `after`.
- [ ] **Step 6:** `vitality-client pause` mid-typing — Expected: one final `after`; no new rows while typing afterwards. `vitality-client pause 1m` then wait — Expected: captures resume after a minute. Restart the daemon while paused — Expected: still paused.
- [ ] **Step 7:** `vitality-client status` and `status --json` — Expected: fields and paths shown; JSON is one line.
- [ ] **Step 8:** Unplug/replug a USB or Bluetooth mouse if available — Expected: activity from it is detected again within ~5 s.
- [ ] **Step 9:** Install and enable the systemd user service; reboot or re-login — Expected: `systemctl --user status vitality-client` is active and captures appear. Leave it for a day (spec §13).
- [ ] **Step 10:** Record any deviations found in `client/README.md` "Known issues" and commit.
