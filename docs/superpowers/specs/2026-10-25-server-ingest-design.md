# Vitality — Server ingest design

**Date:** 2026-10-25
**Status:** Draft for review

## 1. Purpose

Get the laptop's screen captures onto an always-on server, reliably and privately, in a layout
that people and file-based agents can browse easily. This is the foundation that the
interpreter (captures → observations) and the user model will build on later.

The work has three parts:

1. **A shared protocol package** (`protocol/`): the interaction folder layout and the upload
   contract, used by both sides.
2. **Daemon changes** (`client/`): record captures as interaction folders, and upload each
   finished interaction.
3. **A server** (`server/`): receive, check and store interactions on the exe.dev VM.

It updates the client daemon design (`2026-10-21-daemon-design.md`, §4 policy outputs,
§6 storage, §7 status) and implements the "daemon → server" step of the parent design
(`2026-10-21-screen-user-model-design.md`), whose §5 spool and §6.1 ingest it replaces.

## 2. Scope

**In:**
- Interaction folders on the laptop: one folder per interaction, named by UTC start time.
- A one-time migration of the existing local captures into the new layout.
- Uploading each finished interaction over Tailscale to an HTTP API, with retries.
- A FastAPI server on the exe.dev VM that stores interactions in the same layout.
- Device identity from Tailscale, with an allow list.
- `status` and `doctor` reporting on uploads.

**Out (later):**
- The interpreter, observations, the user model, the view, triggers.
- Deleting local captures after upload. Everything is kept locally for now.
- Encryption at rest on the server (see §9).
- Backups of the server's data.
- Multiple users. Several clients are supported by the layout but only the laptop is set up.

## 3. Decisions

| Decision | Choice | Why |
|---|---|---|
| Unit of storage and upload | One **interaction** (a burst of activity) | It is the natural unit for an observation and for an agent to read |
| Interaction name | UTC start time, to the second: `2026-10-25/17-00-00` | Readable, always sorts in true order, travel-proof; ms not needed because interactions start ≥ 2 s apart |
| Local time | Stored inside `interaction.json`, not in names | Keeps names unambiguous; local context still available |
| Shot names | Whole seconds since the interaction started: `0004.webp` | Order and timing readable from `ls` |
| Before/after | Not stored; the shot at `0000` is the screen just before the first input | Redundant once `start` is defined as the first input |
| Manual captures | Join the open interaction; their own one-shot interaction when idle | Interactions never overlap |
| Transport | Tailscale + HTTP API | Private, always on, ready for more devices |
| Upload timing | Each interaction as soon as it ends | Median interaction lasts 9 s, longest seen 2 min 13 s |
| Local copies | Kept after upload | Nothing is deleted until the pipeline is trusted |
| Existing captures | Migrated once by a throwaway script | Real history is valuable for the interpreter |
| Shared contract | A third package, `vitality_protocol` | Shared things at the common level; dependencies point down |
| Server framework | FastAPI (Pydantic models, uvicorn); `httpx` on the laptop | Route signatures are the contract and are enforced |
| Server running | systemd service + an install script in the repo | One service; mirrors the daemon |
| Device identity | Tailscale `whois` of the connection + allow list | Cannot be faked by the sender; no laptop config |
| Uploads while paused | Continue | Pause stops new captures; existing ones were already accepted |

## 4. Repository layout

```
client/     vitality_client     daemon + CLI (exists)
server/     vitality_server     new
protocol/   vitality_protocol   new; shared by both, depends on neither
```

`client/` and `server/` each depend on `protocol/` as a local path dependency. `protocol/`
contains only definitions and pure functions: no network, no disk, no daemon or server logic.
All three target Python 3.14 (the laptop's system Python; `uv` installs it on the VM).

## 5. Protocol package

### 5.1 Folder layout (`vitality_protocol/layout.py`)

```
captures/2026-10-25/17-00-00/      server: captures/<client>/2026-10-25/17-00-00/
  0000.webp                        the screen just before the first input
  0004.webp
  0014.webp
  0014-2.webp                      a second shot in the same second (e.g. a manual capture)
  interaction.json                 written when the interaction ends; present = complete
```

Functions:
- `interaction_name(start: datetime, taken: Container[str]) -> str`: `"YYYY-MM-DD/HH-MM-SS"` in
  UTC; if that name is taken, appends `-2`, `-3`, … (clock jumps backwards, or the rare clash
  with a manual capture).
- `shot_filename(offset_s: int, taken: Container[str]) -> str`: four-digit whole seconds since
  `start`, `.webp`; `-2`, `-3`, … if taken.
- `INTERACTION_FILE = "interaction.json"`.

Shots in one interaction are at least 2 s apart except for manual captures, so clashes are rare.
Four digits cover interactions up to 2 h 46 min.

### 5.2 Messages (`vitality_protocol/messages.py`)

Pydantic models, so data crossing the network is validated when it arrives.

```python
class Shot(BaseModel):
    file: str                                  # "0004.webp"
    ts: datetime                               # exact UTC time
    trigger: Literal["activity", "manual"]
    width: int
    height: int
    bytes: int

class Interaction(BaseModel):                  # the content of interaction.json
    name: str                                  # "2026-10-25/17-00-00"
    source: Literal["screen"]
    start: datetime                            # first input, UTC
    end: datetime                              # last shot, UTC
    local_start: datetime                      # start in local time, with offset
    tz: str                                    # IANA name, e.g. "America/Vancouver"
    shots: list[Shot]                          # in time order

class UploadReply(BaseModel):
    ok: Literal[True] = True
    stored: Literal["new", "existing"]

class ErrorReply(BaseModel):
    ok: Literal[False] = False
    error: str

class Health(BaseModel):
    ok: Literal[True] = True
    disk_free_bytes: int
```

A one-shot interaction from a manual capture while idle has `start` equal to its shot's `ts`;
its shot's `trigger` is `"manual"`, so it is not mistaken for a pre-input screen.

### 5.3 Routes (`vitality_protocol/routes.py`)

| Route | Request | Reply |
|---|---|---|
| `PUT /interactions/{day}/{time}` | multipart: part `interaction` = `Interaction` JSON; one part `shots` per file, filename = `Shot.file` | `201` `UploadReply(stored="new")`, `200` `UploadReply(stored="existing")`, or an error (§7.3) |
| `GET /health` | – | `200` `Health` |

The sending device is not part of the URL: the server learns it from Tailscale (§7.2).
The module holds the path templates as constants and this table as its docstring.

## 6. Daemon changes

### 6.1 Policy: explicit interaction boundaries

Every `SnapshotPolicy` event method returns `list[Snapshot | InteractionEnd]`:

```python
@dataclass(frozen=True)
class InteractionRef:
    start: float                     # first input, epoch seconds; identifies the interaction

@dataclass(frozen=True)
class Snapshot:                      # "take a screenshot now"
    interaction: InteractionRef
    trigger: Literal["activity", "manual"]
    ts: float

@dataclass(frozen=True)
class InteractionEnd:                # "this interaction is over"
    interaction: InteractionRef
    ts: float
```

- An interaction starts at the first input after idle, with a `Snapshot` at that instant.
- It ends with the final settle snapshot, or on pause. `InteractionEnd` is returned even if
  that final snapshot is later dropped for lack of a frame, so every interaction is closed.
- The policy keeps its internal before/after logic for deciding *when* to shoot; it no longer
  labels snapshots with a kind.
- New method `manual(now) -> list[Snapshot | InteractionEnd]`: while active, a `Snapshot` in the
  open interaction with `trigger="manual"`; while idle or paused, a `Snapshot` and an
  `InteractionEnd` for a new one-shot interaction.

### 6.2 Writer: interaction folders

- A `Snapshot` writes `captures/<name>/<offset>.webp` (crash-safe as today: temporary name,
  rename, then index row). The folder name is chosen when the interaction's first shot is
  written, using `interaction_name` against names already in the index.
- An `InteractionEnd` writes `interaction.json` from the index rows (temporary name, then
  rename) and records `end`; the daemon then calls `uploader.wake()`.
- An interaction whose shots were all dropped writes nothing and leaves no folder.
- Local time and zone come from the system clock at `start` (`tz` from `/etc/localtime`).
- **Interrupted interactions:** on startup, any interaction in the index without an `end` is
  closed: `end` = its last shot's `ts`, and `interaction.json` is written.
- Orphaned image files (no index row) are still deleted on startup.

### 6.3 Index: two tables

```
interactions(
  name        TEXT PRIMARY KEY,   -- "2026-10-25/17-00-00"
  source      TEXT NOT NULL,      -- "screen"
  start       TEXT NOT NULL,      -- UTC ISO 8601, ms
  end         TEXT,               -- NULL while open
  local_start TEXT NOT NULL,
  tz          TEXT NOT NULL,
  uploaded_at TEXT,               -- NULL until the server confirms
  upload_error TEXT               -- set when the server rejected this interaction (422)
)
shots(
  interaction TEXT NOT NULL REFERENCES interactions(name),
  file        TEXT NOT NULL,      -- "0004.webp"
  ts          TEXT NOT NULL,
  trigger     TEXT NOT NULL CHECK (trigger IN ('activity','manual')),
  width       INTEGER NOT NULL,
  height      INTEGER NOT NULL,
  bytes       INTEGER NOT NULL,
  PRIMARY KEY (interaction, file)
)
```

`CaptureIndex` gains `pending_uploads() -> list[str]` (ended, not uploaded, no `upload_error`;
oldest first), `mark_uploaded(name, ts)`, `mark_failed(name, error)`, and
`interaction(name) -> Interaction`. Capture counts in `status` count shots.

### 6.4 Uploader (`daemon/upload/`)

```python
class Uploader(Protocol):
    async def run(self) -> None: ...      # until cancelled: upload pending interactions, oldest first
    def wake(self) -> None: ...           # an interaction just ended: check now
    @property
    def status(self) -> UploadStatus: ...

class UploadStatus(TypedDict):
    state: Literal["disabled", "ok", "offline", "rejected"]
    pending: int
    last_upload: str | None              # UTC ISO 8601
```

Config (flat key in `client.toml`, like the existing ones):

```toml
upload_server = "http://vitality-librarian:8100"
```

If `upload_server` is unset, no uploader runs and `status` reports `disabled`.

Loop:
1. Take the oldest pending interaction.
2. `PUT /interactions/<name>` with `interaction.json` and its shots.
3. On `201` or `200`: `mark_uploaded`, continue with the next.
4. When nothing is pending: wait for `wake()`, or 60 s.

One upload at a time. Uploads continue while capture is paused.

Failures:

| Outcome | Response | `state` |
|---|---|---|
| Connection error, timeout, `5xx`, `507` | retry the same interaction after 5 s, doubling to at most 5 min | `offline` |
| `403` | log once; retry every 5 min | `rejected` |
| `409`, `422`, or local files missing/corrupt | `mark_failed` with the reason, log, continue with the next | unchanged |

### 6.5 Status and doctor

- `Status` gains `upload: UploadStatus`. `vitality-client status` prints e.g.
  `upload: ok · 0 pending · last 17:00:41`; `--json` includes it.
- `doctor` checks, when `upload_server` is set: the laptop is on Tailscale, and
  `GET /health` answers.

### 6.6 Code layout changes

```
client/vitality_client/daemon/
  policy/      Snapshot, InteractionEnd, InteractionRef replace SnapshotRequest
  storage/     interaction folders; two-table index
  upload/      new: interface.py (Uploader, UploadStatus), http_uploader.py (HttpUploader)
```

New dependencies: `httpx`, `vitality-protocol` (path dependency).

## 7. Server

### 7.1 Layout

```
server/
  pyproject.toml
  install.sh                     sets up the VM (§7.5)
  systemd/vitality-server.service
  vitality_server/
    app.py                       FastAPI routes; wires identity + storage
    main.py                      entry point: load config, run uvicorn
    config.py                    server.toml
    identity/                    interface.py (Identity), tailscale.py (TailscaleIdentity)
    storage/                     interface.py (InteractionStore), files.py (FileStore)
  tests/
```

### 7.2 Identity

```python
class Identity(Protocol):
    async def who(self, ip: str) -> str | None: ...   # the Tailscale device name, or None
```

`TailscaleIdentity` asks the local `tailscaled` (its LocalAPI `whois`) which device owns the
connecting address. An upload is accepted only if that device is in `allowed_clients`;
otherwise `403`. The device name becomes `<client>` in the storage path. Device names are set
in Tailscale (e.g. the laptop is renamed `laptop`).

### 7.3 Storage and checks

```python
class InteractionStore(Protocol):
    def put(self, client: str, interaction: Interaction,
            files: Mapping[str, bytes]) -> Literal["new", "existing"]: ...
    def disk_free_bytes(self) -> int: ...
```

`FileStore` writes `<data_dir>/captures/<client>/<name>/` and an SQLite index
(`<data_dir>/index.sqlite`) with the same two tables as the laptop, plus `client` and
`received_at` on `interactions`; the key of both tables includes `client`.

Checks, each rejected with `422` and a reason:
- the URL's `{day}/{time}` equals `interaction.name`;
- `interaction.name` matches `interaction_name(start)` (allowing a clash suffix);
- the uploaded file names are exactly the `shots` list, sizes match `bytes`;
- every file starts with a WebP header.

Writing is atomic: files go to `<data_dir>/tmp/<random>/`, then the folder is renamed into
place in one step. Leftover temp folders are deleted on startup.

Repeats: an upload whose name already exists returns `200 existing` if its `interaction.json`
is identical, else `409` (logged as an error; the original is kept). A full disk returns `507`.
Request bodies over 50 MB are rejected (`413`).

### 7.4 Config and network

`/etc/vitality/server.toml`:

```toml
data_dir = "/srv/vitality"
port = 8100
allowed_clients = ["laptop"]
```

The server binds **only to the VM's Tailscale IPv4 address** (read from `tailscale ip -4` at
startup), so only Tailscale devices can connect, and exe.dev's HTTPS proxy (which targets
port 8000 by default) cannot reach it.

### 7.5 Installation

`server/install.sh`, run on the VM from a checkout of the repo:
1. installs Python 3.14 with `uv` and creates the server's virtual environment;
2. creates the `vitality` system user, `/srv/vitality` and `/etc/vitality/server.toml`;
3. installs and enables `vitality-server.service` (`After=tailscaled.service`,
   `User=vitality`, `Restart=on-failure`).

Once per VM, by the user: `sudo tailscale up` (opens a login link), then rename the device
`vitality-librarian`. The laptop joins the same Tailscale network and is renamed `laptop`.

## 8. Migration (one time, not in the repo)

A throwaway script, kept outside the repository, converts the laptop's existing captures:
1. Stop the daemon service.
2. Back up the data directory (to `~/.local/share/vitality/client.bak`).
3. For each existing `interaction_id`: compute `start` (its first capture's `ts`), the name,
   each shot's file name; **rename** the files into place; insert `interactions` and `shots`
   rows; write `interaction.json`. Existing captures get `trigger = "activity"`. For the few
   interactions whose before shot was dropped, the true first-input time is unknown; `start`
   is set to their first shot's time minus `settle_seconds` (2 s), so no after lands at
   `0000` (which would wrongly mark it as the pre-input screen).
4. Drop the old `captures` table.
5. Verify: shot count equals the old capture count; interaction count equals distinct
   `interaction_id`s; spot-check folders.
6. Install the new daemon and start the service; the backlog uploads.
7. Delete the backup once laptop and server agree.

Renames only, so an interruption loses nothing and the script can be rerun.

## 9. Security and privacy

- Screenshots now leave the laptop. They are reachable only over Tailscale, from devices on
  the server's allow list.
- **Known gap:** files are stored unencrypted on the exe.dev VM (New York). Anyone with access
  to that machine could read them. Encryption at rest is deferred to a later version.
- No backups: exe.dev does not back up VMs. The laptop keeps every capture, so the laptop is
  the backup for now.
- Logs on both sides never contain image contents. The server logs the device, the interaction
  name, and the reason for any rejection.
- The future doer VM must not be in `allowed_clients`.

## 10. Testing

- **Protocol:** naming (clash suffixes, offsets, UTC formatting), `Interaction` JSON
  round-trips, invalid data rejected.
- **Policy** (fake clock): `InteractionEnd` on settle, on pause, and when the final snapshot is
  dropped; manual captures join the open interaction or form a one-shot interaction.
- **Writer and index** (temporary directory): folder and file layout, `interaction.json` on
  end, interrupted interactions closed at startup, pending/uploaded/failed queries.
- **Uploader** (fake HTTP server via `httpx.MockTransport`, fake clock): success, `200
  existing`, offline backoff, `403` slow retry, `409`/`422` skip, wake-up on new interactions.
- **CLI:** `status` and `doctor` lines.
- **Server** (FastAPI test client, fake `Identity`, temporary directory): allowed device; device
  not on the allow list; each `422`; `409`; `413`; `507`; temp cleanup after a simulated
  crash.
- Pyright reports 0 errors across all three packages.
- **On the real VM:** the steps in §11.

## 11. Done when

1. The migration is complete; shot and interaction counts match before and after.
2. The server runs on the VM and is reachable only over Tailscale.
3. The backlog has uploaded; laptop and server hold the same interactions.
4. A new interaction appears on the server within about a minute of ending.
5. With the laptop offline, interactions queue and then drain on reconnect.
6. `status` shows the upload state.
7. All tests pass.
