# Vitality — Client daemon design

**Date:** 2026-10-21
**Status:** Draft for review
**Parent spec:** [2026-10-21-screen-user-model-design.md](2026-10-21-screen-user-model-design.md) (§5). Where this spec differs from the parent, this spec wins.

## 1. Purpose

`vitality-client` is a background service on a Linux machine that saves screenshots around the user's keyboard and mouse activity, as before/after pairs, to local storage with a small metadata index. It is built first so that real captures start accumulating for all later work (interpreter prompts, golden set, user-model engine).

## 2. Scope

**In scope**
- Detect input activity (timing only) on any Linux Wayland desktop.
- Capture the whole desktop via the screen-capture portal.
- Before/after capture policy around activity; pause and timed pause.
- Save captures as WebP files plus a SQLite index.
- CLI for control and status; systemd user service.

**Out of scope (later)**
- Uploading to a server (the index reserves an `uploaded_at` column).
- Heartbeat / periodic captures (passive viewing is a known gap).
- Detecting the active monitor; per-monitor captures or monitor ids.
- Dedupe, denylists, redaction, tray icon.
- X11 sessions (a direct-capture backend can be added behind the same frame-source interface).
- Other sources (camera, audio) — the index has a `source` column so they can be added.
- Client identity (assigned server-side later).
- Encryption at rest (relies on full-disk encryption).
- Isolating input-device access into a separate privileged process (§9).

## 3. Components and data flow

One Python (asyncio) process with these units:

| Unit | Does | Interface |
|---|---|---|
| **Input watcher** | Reads keyboard, mouse, and touchpad devices via evdev. Discards key codes and pointer data on read. Rescans on hot-plug. | Emits *activity events*: a timestamp only. |
| **Screen stream** | Opens the screen-capture session once and keeps frames flowing. | Feeds the frame buffer. |
| **Frame buffer** | Holds the most recent whole-desktop frame. | `latest_frame()` → image |
| **Capture policy** | Pure timing logic (§4). No I/O; driven by activity events, pause commands, and an injectable clock. | Emits *snapshot requests*: `(kind, interaction_id, ts)` |
| **Writer** | On a snapshot request, takes `latest_frame()`, encodes WebP, writes the file, inserts the index row. | Consumes snapshot requests. |
| **Control socket** | Unix socket serving pause / resume / status; persists pause state. | Used by the CLI. |
| **CLI** | `vitality-client` subcommands (§7). | Thin client of the control socket (except `doctor` and `reset`). |

**Before shots:** when the first activity event of an interaction arrives, the writer reads the frame buffer immediately. Applications have not yet redrawn in response to that input, so the buffered frame shows the screen as it was before the action.

## 4. Capture policy

States: **idle**, **active**, **paused**.

1. **Idle → activity event:** request `snapshot(before)`, start a new `interaction_id`, enter **active**.
2. **Active → activity event:** restart the settle timer.
3. **Active → no activity for `settle_seconds` (default 2):** request `snapshot(after)`, enter **idle**.
4. **Active → `max_gap_seconds` (default 10) since this interaction's last snapshot, activity continuing:** request `snapshot(after)`, stay **active** in the same interaction. A long session yields before, after, after, …
5. **Pause command:** if **active**, request a final `snapshot(after)` to close the interaction. Enter **paused**; ignore activity.
6. **Timed pause (`pause 30m`):** as 5, then the policy schedules its own resume.
7. **Resume:** enter **idle**.

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
  captures/2026-10-21/<capture_id>.webp
  index.sqlite
```

**Images:** whole desktop, native resolution, lossy WebP, quality 85 (configurable).

**Table `captures`:**

| Column | Type | Meaning |
|---|---|---|
| `capture_id` | text, primary key | ULID (sorts by time); also the filename |
| `interaction_id` | text | Shared by the before/after captures of one interaction (ULID) |
| `kind` | text | `before` or `after` |
| `source` | text | `screen` (only value in v0) |
| `ts` | text | Capture time, UTC, ISO 8601 with milliseconds |
| `path` | text | Path relative to the data directory |
| `width`, `height` | integer | Pixel dimensions |
| `bytes` | integer | File size |
| `uploaded_at` | text, nullable | Always NULL in v0; reserved for upload |

Indexes on `ts` and `uploaded_at`.

**Crash safety:** the image is written to a temporary name, renamed into place, then the row is inserted. A row never points to a missing file. On startup, image files without a row are deleted.

**Disk use:** `status` reports total size; past `disk_warning_gb` (default 20) it warns in `status` and the log. Nothing is deleted automatically.

## 7. Control and running

Two commands from one package: **`vitality-clientd`** is the daemon (what the service runs);
**`vitality-client`** controls it.

**CLI (`vitality-client`)**
| Command | Does |
|---|---|
| `pause [DURATION]` | Pause, optionally for e.g. `30m`, `2h` |
| `resume` | Resume |
| `status` | Human-readable: state, captures today, last capture time, disk use, warnings, and the config/data/state paths |
| `status --json` | Same as one line of JSON, for a status bar |
| `doctor` | Checks setup (§8) and explains anything missing |
| `capture` | Asks the daemon for one screenshot now (kind `after`, its own interaction, even while paused) and prints its path |
| `toggle` | Pause if capturing, resume if paused |
| `reset [--all] [--yes]` | Delete all captures (and with `--all` the state); refuses while the daemon runs |

Control socket: `$XDG_RUNTIME_DIR/vitality/client.sock`.
State (pause state, portal restore token): `$XDG_STATE_HOME/vitality/client/state.json`.

**Configuration:** optional `~/.config/vitality/client.toml`; defaults apply when absent.

```toml
settle_seconds = 2
max_gap_seconds = 10
webp_quality = 85
disk_warning_gb = 20
```

**Service:** systemd user unit `vitality-client.service` running `vitality-clientd`, `Restart=on-failure`, started with the graphical session. Logs to the journal.

## 8. Setup

Files follow the XDG Base Directory convention (config, data, and state in separate locations, so settings can live in a dotfiles repo without the captures). `status` and `doctor` print all three paths.

`vitality-client doctor` checks each of these:
1. The user is in the `input` group (required to read `/dev/input`; takes effect after re-login).
2. The session is Wayland, and xdg-desktop-portal with a ScreenCast implementation, PipeWire, and the GStreamer PipeWire plugin are present.
3. Data, state, and runtime directories are writable.

First run: approve the screen-capture dialog once.

## 9. Security and privacy notes

- Membership in the `input` group lets **every** process of the user read all input devices, weakening Wayland's protection against keylogging. Accepted for v0 on the author's own machine.
- The input watcher is a small, self-contained module that reduces each event to a timestamp before it leaves the module, so it can later be split into a separate process running as a dedicated system user. That split is required before Vitality runs on anyone else's machine.
- Logs never contain key data, pointer data, or image contents.
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
    config.py          # shared: paths and client.toml settings
    control/           # shared: socket channel, saved state, durations -> send_command, ControlServer, StateFile
    daemon/            # vitality-clientd and the parts only it uses
      daemon.py        #   contract (Daemon, Status) at the top, CaptureDaemon below
      main.py          #   the vitality-clientd entry point
      policy/          #   WHEN to capture (pure logic)        -> CapturePolicy, SnapshotRequest
      activity/        #   input timing via evdev              -> ActivityWatcher
      frames/          #   whole-desktop images                -> FrameSource (interface)
        portal/        #     portal implementation             -> PortalFrameSource
          session.py   #       D-Bus conversation with the portal
          stream.py    #       GStreamer pipelines, newest-frame buffer
          source.py    #       combines the two; retry/backoff
      storage/         #   WebP files + SQLite index           -> Writer, Store
    cli/               # vitality-client: one module per command group; checks.py holds doctor's checks
  tests/
  systemd/vitality-client.service
```

Dependencies only point down the tree: a package's children are what it alone uses, and
anything two parts share sits at their common level. `daemon/` and `cli/` never import
each other; both use `control/` and `config.py`. Each package's `interface.py` holds its
contract and its `__init__.py` says what it is.

**Dependencies:** `evdev`, a D-Bus client library for the portal (`dbus-next`), PyGObject with GStreamer and its PipeWire plugin (system packages), `Pillow`, `python-ulid`.

## 12. Testing

- **Capture policy** (fake clock, scripted activity): single burst → before + after; continuous activity → before, then after every 10 s, then after on settle; pause while active → final after then nothing; pause while idle → nothing; timed pause resumes; pause state survives restart; idle → no snapshots.
- **Input watcher** (fake devices and events): output contains timestamps only — no key codes or pointer values can leave the module.
- **Writer and store** (temporary directory, fake images): file layout, row contents, file-before-row ordering, orphan cleanup on startup.
- **Control** (temporary socket): pause, timed pause, resume, status and status --json round-trips.
- **Capture backend:** verified manually with `vitality-client capture` and `doctor` on the author's Arch/Hyprland laptop.

## 13. Done when

- `vitality-client` runs as a user service on the author's laptop for a full day.
- Captures appear as before/after pairs around activity, with forced afters during long activity, and none while paused.
- `status --json` drives a status-bar indicator.
- The index and image files are consistent and readable.
