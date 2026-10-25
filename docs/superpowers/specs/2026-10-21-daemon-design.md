# Vitality — Client daemon design

**Date:** 2026-10-21
**Status:** Draft for review
**Updated:** §4 (capture policy), §6 (storage), §7 (control and status) and §11 (code layout) were updated by [2026-10-25-server-ingest-design.md](2026-10-25-server-ingest-design.md): captures are now grouped into interaction folders and uploaded to a server. Where the two differ, the newer spec wins. Smaller edits in §§2, 3, 8 and 12 keep them consistent.
**Parent spec:** [2026-10-21-screen-user-model-design.md](2026-10-21-screen-user-model-design.md) (§5). Where this spec differs from the parent, this spec wins.

## 1. Purpose

`vitality-client` is a background service on a Linux machine that saves screenshots around the user's keyboard and mouse activity, in one folder per interaction, to local storage with a small metadata index. It is built first so that real captures start accumulating for all later work (interpreter prompts, golden set, user-model engine).

## 2. Scope

**In scope**
- Detect input activity (timing only) on any Linux Wayland desktop.
- Capture the whole desktop via the screen-capture portal.
- Capture policy around bursts of activity (interactions); pause and timed pause.
- Save captures as WebP files, in one folder per interaction, plus a SQLite index.
- CLI for control and status; systemd user service.

**Out of scope (later)**
- Uploading to a server was out of scope here; it is now covered by the server-ingest spec (§6.4 there).
- Heartbeat / periodic captures (passive viewing is a known gap).
- Detecting the active monitor; per-monitor captures or monitor ids.
- Dedupe, denylists, redaction, tray icon.
- X11 sessions (a direct-capture backend can be added behind the same frame-source interface).
- Other sources (camera, audio) — the index has a `source` column so they can be added.
- Client identity in the daemon: the server learns which device uploaded from Tailscale, so the client sends none (server-ingest spec §7.2).
- Encryption at rest (relies on full-disk encryption).
- Isolating input-device access into a separate privileged process (§9).

## 3. Components and data flow

One Python (asyncio) process with these units:

| Unit | Does | Interface |
|---|---|---|
| **Input watcher** | Reads keyboard, mouse, and touchpad devices via evdev. Discards key codes and pointer data on read. Rescans on hot-plug. | Emits *activity events*: a timestamp only. |
| **Screen stream** | Opens the screen-capture session once and keeps frames flowing. | Feeds the frame buffer. |
| **Frame buffer** | Holds the most recent whole-desktop frame. | `latest_frame()` → image |
| **Capture policy** | Pure timing logic (§4). No I/O; driven by activity events, pause commands, and an injectable clock. | Emits *actions*: `Snapshot(interaction, trigger, ts)` and `InteractionEnd(interaction, ts)` |
| **Writer** | On a `Snapshot`, takes `latest_frame()`, encodes WebP, writes the file in the interaction's folder, inserts the index row; on an `InteractionEnd`, writes `interaction.json`. | Consumes actions. |
| **Uploader** | Sends each ended interaction to the server and records the result in the index (§6, and the server-ingest spec §6.4). | `run()`, `wake()`, `status` |
| **Control socket** | Unix socket serving pause / resume / status; persists pause state. | Used by the CLI. |
| **CLI** | `vitality-client` subcommands (§7). | Thin client of the control socket (except `doctor` and `reset`). |

**First shots:** when the first activity event of an interaction arrives, the writer reads the frame buffer immediately. Applications have not yet redrawn in response to that input, so the buffered frame shows the screen as it was before the action. It is the interaction's first shot, at offset `0000`.

## 4. Capture policy

States: **idle**, **active**, **paused**. The policy's outputs are actions: a `Snapshot` ("take a screenshot now", with its `trigger`, `activity` or `manual`) and an `InteractionEnd` ("this interaction is over"). Both carry an `InteractionRef`, the interaction's start time (its first input), which identifies the interaction.

1. **Idle → activity event:** start an interaction (its start is this event's time), emit a `Snapshot` at that instant, enter **active**. This first shot is the screen just before the input.
2. **Active → activity event:** restart the settle timer.
3. **Active → no activity for `settle_seconds` (default 2):** emit a final `Snapshot` and an `InteractionEnd`, enter **idle**.
4. **Active → `max_gap_seconds` (default 10) since this interaction's last snapshot, activity continuing:** emit a `Snapshot` in the same interaction, stay **active**. A long session yields a shot every 10 s until it settles.
5. **Pause command:** if **active**, emit a final `Snapshot` (unless one was just due) and an `InteractionEnd`. Enter **paused**; ignore activity.
6. **Timed pause (`pause 30m`):** as 5, then the policy schedules its own resume.
7. **Resume:** enter **idle**.
8. **Manual capture (`capture`):** while **active**, emit a `Snapshot` with `trigger="manual"` in the open interaction. While **idle** or **paused**, emit a `Snapshot` and an `InteractionEnd` for a new one-shot interaction starting now. It never changes the state or the timers.

`InteractionEnd` is emitted even if the interaction's last snapshot is later dropped for lack of a frame, so every interaction is closed.

Pause state (including a timed-pause deadline) persists across daemon restarts: a reboot never silently resumes capture. An expired timed pause resumes on startup.

## 5. Screen capture

Wayland only. Produces one **whole-desktop image**: all monitors placed according to their layout. No monitor identity is recorded. If `XDG_SESSION_TYPE` is not `wayland`, the daemon exits with an explanatory error.

**Portal + PipeWire + GStreamer**
- Request a ScreenCast session from xdg-desktop-portal for all monitors, with the cursor embedded in the stream, and persistence enabled.
- First run shows the desktop's permission dialog; the returned restore token is saved so later runs start without it.
- Each monitor arrives as a PipeWire video stream, read through a GStreamer pipeline (`pipewiresrc` → convert to RGB → app sink keeping only the newest frame).
- Streams are capped at ~5 fps; compositors also only send frames when the screen changes, so idle cost is negligible.
- `latest_frame()` composites the newest frame of each monitor using the positions and sizes reported by the portal.

**Limits and failure**
- If the composite exceeds WebP's 16,383 px per side, it is scaled down just enough to fit.
- If the capture session is lost or setup fails, `status` reports `capture: unavailable`; the daemon retries with backoff. Snapshot requests during that time are dropped and logged.

## 6. Storage

Data directory: `$XDG_DATA_HOME/vitality/client/` (default `~/.local/share/vitality/client/`).

```
client/
  captures/2026-10-25/17-00-00/     one folder per interaction, named by its UTC start time
    0000.webp                       the screen just before the first input
    0004.webp                       later shots: whole seconds since the start
    0014.webp
    interaction.json                written when the interaction ends; present = complete
  index.sqlite
```

The naming functions live in the shared `vitality_protocol` package, so the server stores the same layout. A clash (same second, or a manual capture) gets a `-2`, `-3`, … suffix. `interaction.json` holds the interaction's start, end, local start time, IANA time zone and the list of its shots.

**Images:** whole desktop, native resolution, lossy WebP, quality 85 (configurable).

**Table `interactions`:**

| Column | Type | Meaning |
|---|---|---|
| `name` | text, primary key | `2026-10-25/17-00-00` (also the folder, relative to `captures/`) |
| `source` | text | `screen` (only value) |
| `start` | text | First input, UTC, ISO 8601 with milliseconds |
| `end` | text, nullable | Last shot's time; NULL while the interaction is open |
| `local_start`, `tz` | text | `start` in local time with its offset, and the IANA zone name |
| `uploaded_at` | text, nullable | NULL until the server confirms the upload |
| `upload_error` | text, nullable | Set when the server rejected the interaction for good; it is then no longer pending |

**Table `shots`:**

| Column | Type | Meaning |
|---|---|---|
| `interaction`, `file` | text | Primary key together: the interaction's `name` and the file name (`0004.webp`) |
| `ts` | text | Capture time, UTC, ISO 8601 with milliseconds |
| `trigger` | text | `activity` or `manual` |
| `width`, `height` | integer | Pixel dimensions |
| `bytes` | integer | File size |

Index on `shots(ts)` and `interactions(uploaded_at)`. Capture counts in `status` count shots.

**Crash safety:** a shot's image is written to a temporary name, renamed into place, then its row is inserted, so a row never points to a missing file. `interaction.json` is written the same way (temporary name, rename) before the interaction's `end` is recorded. On startup, image files without a row are deleted, and any interaction without an `end` (the daemon crashed mid-interaction) is closed: its `end` becomes its last shot's time and its `interaction.json` is written. An interaction whose shots were all dropped (no frame available) leaves no folder.

**Upload:** with `upload_server` set (§7), the uploader sends each ended, not yet uploaded interaction, oldest first, and sets `uploaded_at`. Captures are kept locally after they upload.

**Disk use:** `status` reports total size; past `disk_warning_gb` (default 20) it warns in `status` and the log. Nothing is deleted automatically.

## 7. Control and running

Two commands from one package: **`vitality-clientd`** is the daemon (what the service runs);
**`vitality-client`** controls it.

**CLI (`vitality-client`)**
| Command | Does |
|---|---|
| `pause [DURATION]` | Pause, optionally for e.g. `30m`, `2h` |
| `resume` | Resume |
| `status` | Human-readable: state, captures today, last capture time, disk use, activity and capture availability, the `upload` line, warnings, and the config/data/state paths |
| `status --json` | Same as one line of JSON, for a status bar |
| `doctor` | Checks setup (§8) and explains anything missing |
| `capture` | Asks the daemon for one screenshot now (even while paused) and prints its path. It joins the open interaction while one is active, and is its own one-shot interaction otherwise |
| `toggle` | Pause if capturing, resume if paused |
| `reset [--all] [--yes]` | Delete all captures (and with `--all` the state); refuses while the daemon runs |

`status` prints one line for uploads, `upload: ok · 0 pending · last 17:00:43`. Its state is `disabled` (no `upload_server`; the line is then just `upload: disabled`), `ok`, `offline` (server unreachable; interactions queue) or `rejected` (this device is refused by the server). `pending` counts ended interactions not yet uploaded. `status --json` carries the same as an `upload` object with `state`, `pending` and `last_upload`.

Control socket: `$XDG_RUNTIME_DIR/vitality/client.sock`.
State (pause state, portal restore token): `$XDG_STATE_HOME/vitality/client/state.json`.

**Configuration:** optional `~/.config/vitality/client.toml`; defaults apply when absent.

```toml
settle_seconds = 2
max_gap_seconds = 10
webp_quality = 85
disk_warning_gb = 20
# upload_server = "http://vitality-librarian:8100"   # unset: no uploads
```

**Service:** systemd user unit `vitality-client.service` running `vitality-clientd`, `Restart=on-failure`, started with the graphical session. Logs to the journal.

## 8. Setup

Files follow the XDG Base Directory convention (config, data, and state in separate locations, so settings can live in a dotfiles repo without the captures). `status` and `doctor` print all three paths.

`vitality-client doctor` checks each of these:
1. The user is in the `input` group (required to read `/dev/input`; takes effect after re-login).
2. The session is Wayland, and xdg-desktop-portal with a ScreenCast implementation, PipeWire, and the GStreamer PipeWire plugin are present.
3. Data, state, and runtime directories are writable.
4. When `upload_server` is set: the laptop is on Tailscale, and the server answers `GET /health`.

First run: approve the screen-capture dialog once.

## 9. Security and privacy notes

- Membership in the `input` group lets **every** process of the user read all input devices, weakening Wayland's protection against keylogging. Accepted for v0 on the author's own machine.
- The input watcher is a small, self-contained module that reduces each event to a timestamp before it leaves the module, so it can later be split into a separate process running as a dedicated system user. That split is required before Vitality runs on anyone else's machine.
- Logs never contain key data, pointer data, or image contents.
- With `upload_server` set, screenshots now leave the laptop for the server, over Tailscale (see the server-ingest spec §9).
- Pausing captures one final frame if an interaction is active; the user should pause before, not after, sensitive content appears.

## 10. Errors

| Situation | Behavior |
|---|---|
| No permission to read input devices | Daemon runs; no activity detected, so no captures; `status`: `activity: unavailable` |
| Capture backend unavailable | `status`: `capture: unavailable`; retries with backoff |
| A single encode or write fails | Logged and skipped; daemon continues |
| Daemon crash | systemd restarts it; pause state is restored from disk |

## 11. Code layout

In `client/` of the Vitality repo. Python ≥ 3.12, managed with `uv`.

```
client/
  pyproject.toml
  vitality_client/
    shared/            # used by both programs
      config.py        #   paths and client.toml settings
      state.py         #   saved pause state + restore token (contract at top) -> StateFile
      channel.py       #   CLI <-> daemon socket protocol      -> send_command, ControlServer
    daemon/            # vitality-clientd and the parts only it uses
      daemon.py        #   contract (Daemon, Status) at the top, CaptureDaemon below
      main.py          #   the vitality-clientd entry point
      policy/          #   WHEN to capture (pure logic)        -> CapturePolicy, Snapshot, InteractionEnd
      activity/        #   input timing via evdev              -> ActivityWatcher
      frames/          #   whole-desktop images                -> FrameSource (interface)
        portal/        #     portal implementation             -> PortalFrameSource
          session.py   #       D-Bus conversation with the portal
          stream.py    #       GStreamer pipelines, newest-frame buffer
          source.py    #       combines the two; retry/backoff
      storage/         #   interaction folders + SQLite index  -> Writer, Store
      upload/          #   sends ended interactions to the server -> Uploader (interface), HttpUploader
    cli/               # vitality-client: one module per command group; checks.py holds doctor's
                       #   checks, duration.py parses pause durations
  tests/
  systemd/vitality-client.service
```

The folder layout and the upload messages live in the shared `protocol/` package at the repo root (`vitality_protocol`), which `client/` depends on as a local path dependency, as does `server/`.

Dependencies only point down the tree: a package's children are what it alone uses, and
anything two parts share sits at their common level. `daemon/` and `cli/` never import
each other; both use `shared/`. Each package's `interface.py` holds its
contract and its `__init__.py` says what it is.

**Dependencies:** `evdev`, a D-Bus client library for the portal (`dbus-fast`), PyGObject with GStreamer and its PipeWire plugin (system packages), `Pillow`, `python-ulid`, `httpx` (uploads), and `vitality-protocol` (path dependency).

## 12. Testing

- **Capture policy** (fake clock, scripted activity): single burst → first `Snapshot`, then a final `Snapshot` and `InteractionEnd` on settle; continuous activity → a `Snapshot` every 10 s, then the final one on settle; pause while active → final `Snapshot` and `InteractionEnd` then nothing; pause while idle → nothing; manual capture joins the open interaction or forms a one-shot one; timed pause resumes; pause state survives restart; idle → no snapshots.
- **Input watcher** (fake devices and events): output contains timestamps only — no key codes or pointer values can leave the module.
- **Writer and store** (temporary directory, fake images): folder and file layout, row contents, file-before-row ordering, `interaction.json` on end, orphan cleanup and closing of interrupted interactions on startup, pending/uploaded/failed queries.
- **Control** (temporary socket): pause, timed pause, resume, status and status --json round-trips.
- **Capture backend:** verified manually with `vitality-client capture` and `doctor` on the author's Arch/Hyprland laptop.

## 13. Done when

- `vitality-client` runs as a user service on the author's laptop for a full day.
- Captures appear as interaction folders around activity, with a shot every 10 s during long activity, and none while paused.
- `status --json` drives a status-bar indicator.
- The index and image files are consistent and readable.
