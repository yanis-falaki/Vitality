# Vitality — Daemon (client) design

**Date:** 2026-10-21
**Status:** Draft for review
**Parent spec:** [2026-10-21-screen-user-model-design.md](2026-10-21-screen-user-model-design.md) (§5)

## 1. Purpose

A background service on a Linux machine that takes screenshots when the user is active, plus a regular heartbeat, and saves them locally with metadata. It is the first piece of Vitality to build because every later step (interpreter prompts, golden set, engine) needs days of real captures.

## 2. Scope

**In scope**
- Detect user input activity (timing only).
- Capture the screen on any Linux desktop (Wayland via portal, X11 directly).
- Capture policy: burst captures after activity + heartbeat.
- Save captures as WebP files with a local SQLite index.
- Pause / resume / status via CLI; status output usable by a status bar.
- Run as a systemd user service.

**Out of scope (later)**
- Uploading to a processing server (the index has an `uploaded_at` column reserved for it).
- Dedupe (server-side per the parent spec).
- Any other source (audio, browser URLs, notifications), denylists, redaction, tray icon.
- Encryption at rest of local captures (relies on full-disk encryption for now).

## 3. Behavior

**Activity detection.** The daemon reads all keyboard, mouse, and touchpad devices via evdev and records only *that* an input event happened and when. Key codes, characters, and pointer positions are discarded on read and never stored, logged, or transmitted. Devices are re-scanned when hot-plugged.

**Capture policy** (all values configurable):
| Rule | Default |
|---|---|
| Burst capture fires once input has been quiet for the *settle delay* | 1.0 s |
| Minimum gap between burst captures during continuous activity (a capture is forced once this elapses even if input never settles) | 5 s |
| Heartbeat: capture if no capture of any kind in the last | 60 s |
| While paused | no captures |

A single capture event captures every monitor; each monitor produces its own capture record sharing a `capture_group` id.

**Pause.** `vitality pause [DURATION]` stops capturing immediately (optionally for a duration, e.g. `30m`, then auto-resumes). `vitality resume` restarts. Pause state persists across daemon restarts — if the user paused, a reboot does not silently resume capture.

**Status.** `vitality status` prints state (capturing / paused / paused until …, captures today, last capture time). `vitality status --json` prints a single-line JSON object suitable for a Waybar/Polybar custom module, so the user can always see whether capture is on.

**Manual test.** `vitality capture-once` takes one capture of all monitors and prints the saved paths — used to verify setup.

## 4. Screen capture backends

Selected at startup from `XDG_SESSION_TYPE`.

- **Wayland — portal backend.** Uses xdg-desktop-portal's ScreenCast interface over D-Bus. On first run the user approves monitor selection in the desktop's dialog; the returned restore token is saved so later runs need no prompt. Frames are read from the resulting PipeWire stream (GStreamer `pipewiresrc` → app sink); the daemon keeps the stream open and grabs the latest frame when the policy fires. Works on GNOME, KDE, Hyprland, sway, and other compositors with a ScreenCast portal implementation.
- **X11 — direct backend.** Captures each monitor with `mss`.

If the portal session ends (e.g. the user revokes it), the daemon logs the error, reports it in `status`, and retries session setup with backoff.

## 5. Storage

Data directory: `$XDG_DATA_HOME/vitality/` (default `~/.local/share/vitality/`).

```
vitality/
  captures/2026-10-21/<capture_id>.webp
  index.sqlite
```

**Images:** downscaled so the longest side is at most 1920 px, WebP quality 80 (both configurable). Expected volume roughly 100–200 MB/day.

**Index table `captures`:**
| Column | Notes |
|---|---|
| `capture_id` | ULID (time-sortable) |
| `capture_group` | shared by all monitors of one capture event |
| `client_id` | stable id for this machine, generated on first run |
| `source` | `"screen"` |
| `monitor` | monitor name/index |
| `ts` | UTC timestamp |
| `trigger` | `"burst"` or `"heartbeat"` |
| `path` | relative path to the WebP file |
| `width`, `height`, `bytes` | image metadata |
| `uploaded_at` | NULL; reserved for future upload |

The image is written first, then the index row, so a crash never leaves an index row pointing at a missing file. Orphaned files without rows are cleaned on startup.

**Disk usage:** `status` reports total size; a configurable warning threshold (default 20 GB) is logged and shown in status. No automatic deletion.

## 6. Structure

Located in `client/` of the Vitality monorepo. Python ≥ 3.12, managed with `uv`.

```
client/
  pyproject.toml
  vitality_client/
    main.py            # entry point: CLI + daemon startup, asyncio orchestration
    config.py          # load ~/.config/vitality/client.toml with defaults
    activity.py        # evdev device discovery + activity timestamps (no key data)
    policy.py          # pure capture-timing logic, driven by an injectable clock
    capture/
      base.py          # Capturer interface: capture() -> list of (monitor, image)
      portal.py        # Wayland portal + PipeWire backend
      x11.py           # X11 backend
    store.py           # WebP encoding, file layout, SQLite index
    control.py         # Unix socket server/client for pause/resume/status
  tests/
  systemd/vitality.service
```

**Units and boundaries:**
- `activity` emits "input happened at t" events; knows nothing about capturing.
- `policy` consumes activity events and clock ticks, emits "capture now (burst|heartbeat)"; pure logic, no I/O.
- `capture` backends implement one interface; `main` picks one.
- `store` persists whatever it is given; knows nothing about timing.
- `control` exposes pause/resume/status over a Unix socket at `$XDG_RUNTIME_DIR/vitality/daemon.sock`; the CLI subcommands are thin clients of it. Pause state is stored in `$XDG_STATE_HOME/vitality/state.json` alongside the portal restore token.

**Dependencies:** `evdev`, `dbus-next` (portal D-Bus calls), PyGObject + GStreamer with the PipeWire plugin (system packages), `mss`, `Pillow`, `python-ulid`.

## 7. Setup requirements

- User is in the `input` group (needed to read `/dev/input`). The README documents this and `vitality doctor` checks it.
- On Wayland: a running xdg-desktop-portal with a ScreenCast backend, PipeWire, and the GStreamer PipeWire plugin. `vitality doctor` checks these.
- Install as a systemd user service: `systemctl --user enable --now vitality`.

## 8. Errors and logging

- Logs go to stdout/stderr (collected by journald under the user service).
- Logs never contain input event details or image content.
- A failed capture is logged and skipped; the daemon keeps running.
- Missing input permission: the daemon still runs heartbeat captures and reports `activity: unavailable` in status.
- systemd restarts the daemon on crash.

## 9. Testing

- **policy:** unit tests with a fake clock and scripted activity sequences (idle, single burst, continuous typing, pause/resume, pause with duration, heartbeat).
- **store:** unit tests on a temp directory (layout, index rows, write ordering, orphan cleanup).
- **activity:** tests with fake devices/event streams, including assertions that no key codes reach any output.
- **control:** pause/resume/status round-trip over a temp socket.
- **capture backends:** manual verification via `vitality capture-once` and `vitality doctor` on the target machine (Hyprland/Wayland); X11 path tested where available.

## 10. Done when

- The daemon runs as a user service on the author's Arch/Hyprland laptop for a full day.
- Captures appear on activity and at least once a minute, for every approved monitor, and none while paused.
- `vitality status --json` works in a status-bar module.
- The day's captures and index are readable and consistent.
