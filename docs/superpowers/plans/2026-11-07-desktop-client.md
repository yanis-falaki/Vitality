# Desktop Client Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
> **Owner's execution preference:** one implementer subagent per milestone, no per-task reviewer cycle; the controller checks results itself.

**Goal:** A new Linux capture client, `vitality-client`: one Tauri app (Rust core, React interface) with a tray icon, named screens, the R1–R8 capture policy, folder storage and upload in the existing protocol, setup, status, settings and a small CLI.

**Architecture:** One process. Rust modules `screens`, `activity`, `session`, `policy`, `store`, `upload`, `control`, `health`, `settings`, wired by a `runtime` that feeds input and frames to the pure `policy` state machine and executes its decisions. The interface is a Tauri webview (React + TypeScript, Vite) talking to the core with typed Tauri commands and events. A systemd user service starts it at login.

**Tech Stack:** Rust (stable), Tauri 2 (tray icon via its Linux StatusNotifierItem support), `ashpd` (xdg-desktop-portal ScreenCast), `gstreamer`/`gstreamer-app` (pipewiresrc → appsink, `always-copy=true`), `evdev`, `zbus` (logind), `webp` (libwebp, quality setting), `reqwest` (multipart, rustls), `tokio`, `serde`/`serde_json`/`toml`; React 18 + TypeScript + Vite + TanStack Query in `desktop/ui`; Playwright (Python venv already on the hub) for interface screenshots against mocked Tauri IPC (`@tauri-apps/api/mocks`).

**Spec:** `docs/superpowers/specs/2026-11-07-desktop-client-design.md` (all section numbers below refer to it).

## Global Constraints

- Upload protocol unchanged except: `Shot.trigger` ∈ {`activity`, `manual`, `passive`}; optional `Shot.screen_name` (written only with more than one screen). Requests: `POST /interactions`, multipart, part `interaction` (the `Interaction` JSON, exactly the fields of `protocol/vitality_protocol/messages.py`), one part `shots` per file with filename = `Shot.file`; status handling exactly as `protocol/vitality_protocol/routes.py` documents (201/200 mark uploaded; 403 keep pending, retry every 5 min, state "rejected"; 409/413/422 mark failed, never retried; 411/500/507/other/timeouts offline, backoff 5 s doubling to 5 min).
- Layout exactly as `protocol/vitality_protocol/layout.py`: `<day>/<HH-MM-SS>` UTC folder names with `-<n>` suffix if taken; shot files `NNNN.webp` (whole seconds since start, `-<n>` if taken); `interaction.json` written last. Marker: empty file `uploaded` (and `failed` for 409/413/422).
- Paths (§10): captures `~/.local/share/vitality/client/captures/`; settings `~/.config/vitality/client.toml`; screens `~/.local/state/vitality/client/screens.json`; pause state `~/.local/state/vitality/client/state.json`; decisions log `~/.local/state/vitality/client/capture.log` (last 500 lines); socket `$XDG_RUNTIME_DIR/vitality/client.sock`.
- Policy defaults (§6): start gap 2 s, settle 2 s, max gap 10 s, change threshold: >0.5 % of a 64×36 grayscale thumbnail differing by >16 levels, passive check every 45 s, passive end after 3 checks without change, size budget (R9) 40 MiB per interaction (`max_interaction_mib`; also at most 900 shots). All are settings.
- Frames (§5): `pipewiresrc ... always-copy=true`, cap 2 fps per feed, newest frame per screen in memory only.
- Portable only: no compositor-specific code (no hyprctl, no grim). Lock and sleep are not watched (owner's decision): R8 is pause only; an interaction left open across a system sleep ends at the first tick after waking, with no shots for the gap.
- The app never runs `sudo`. No self-updater. Nothing published.
- Rust modules and TS components start with a short contract comment (what it does, its interface); small focused files; match the repo's comment style. Commits on branch `desktop`, messages `desktop: …` / `protocol: …` / `server: …` / `observer: …`; never any Co-Authored-By or AI attribution.
- Tests never touch real data, the VM, the laptop or the desktop.

## Review Focus

1. A portal permission that is refused at start for one screen: the other screens keep capturing and the card says "Needs to be picked again" (Task 5 test with a fake portal).
2. An interaction folder left without `interaction.json` (crash): finished from its shots at the next start and uploaded (Task 6 test).
3. Server unreachable for hours, then back: everything pending uploads in order, nothing duplicated (Task 6 test with a fake server that fails then succeeds).
4. Pause while an interaction is open: it ends at once; nothing is captured until resume; passive checks stop (Task 3 policy test + Task 7 control test).
5. The interface when the core reports zero screens, or a failing health check: Setup is shown, not an empty Status (Task 9 UI test).

---

## Milestone 1 — the shell: core, capture, tray, Screens, upload

### Task 1: Protocol additions, server, observer (Python)

**Files:** Modify `protocol/vitality_protocol/messages.py` (`Shot.trigger` adds `"passive"`; `Shot.screen_name: str | None = None`), `server/` (accepts them; `interaction.json` written without `None` fields so one-screen files look as before), `observer/vitality_observer/request.py` (image line gains `· <screen_name>` and `· passive`; prompt gains two sentences per §9). Tests in each package.

- [ ] Tests: protocol round-trip with and without the new fields; server stores a two-screen passive interaction and an old-style one unchanged; observer line `"0012.webp, 12 s after the first input · Main · passive:"` and unchanged lines without the fields; prompt mentions "named by the owner" and "passive".
- [ ] Run each package's `uv run pytest` and `uv run pyright` → pass. Commit `protocol, server, observer: passive shots and screen names`.

### Task 2: The app skeleton

**Files:** Create `desktop/` (Cargo workspace, `src-tauri/` the app crate `vitality-client`, `ui/` the Vite React app), `desktop/README.md`, `desktop/packaging/vitality-client.service` (systemd user unit: `ExecStart=/usr/bin/vitality-client`, `Restart=on-failure`, `PartOf=graphical-session.target`, `After=graphical-session.target`), `desktop/packaging/vitality-client.desktop`, an icon set (simple circle mark in the theme's colours).

**Interfaces — produces:** `main()` dispatch: no args → app; `status | pause [MINUTES] | resume | doctor` → CLI (Task 7/9 fill them); `ui/src/theme.css` copied from `web/app/src/theme.css` (same tokens; do not import across packages); typed IPC module `ui/src/core.ts` wrapping `invoke`/`listen` with TypeScript types mirrored from Rust `serde` structs (one `types.rs` ↔ `types.ts`, kept in sync by a test that serialises sample values and checks the TS fixtures).

- [ ] Install the toolchain on the hub (rustup stable; apt: `libwebkit2gtk-4.1-dev libayatana-appindicator3-dev librsvg2-dev libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev libwebp-dev libudev-dev libssl-dev pkg-config`; Node is at `~/.local/node/bin`).
- [ ] `cargo test`, `cargo clippy -- -D warnings`, `npm run typecheck`, `npm run build` pass on the empty skeleton. Commit `desktop: the app skeleton`.

### Task 3: `policy` — R1–R8 as a pure state machine, and change detection

**Files:** `src-tauri/src/policy/{mod.rs,rules.rs,change.rs}`; tests in the same modules.

**Interfaces — produces:**
- `change::Thumb` (64×36 grayscale) and `change::thumb(image: &RgbImage) -> Thumb`, `change::changed(a: &Thumb, b: &Thumb, t: &Threshold) -> bool` (>`t.fraction` of pixels differ by >`t.levels`; defaults 0.005 and 16).
- `PolicySettings { start_gap, settle, max_gap: Duration, threshold: Threshold, passive_every: Duration, passive_quiet_checks: u32 }` with the §6 defaults.
- `Policy::new(PolicySettings)`; inputs `Policy::on_input(now)`, `Policy::on_tick(now, thumbs: &BTreeMap<ScreenId, Thumb>)`, `Policy::on_stop(now, why: StopReason)` (`Paused | Locked | Sleeping | Quit`), `Policy::on_resume(now)`; each returns `Vec<Decision>`: `StartInteraction{kind: Active|Passive}`, `Shot{screens: Vec<ScreenId>, trigger: Activity|Passive, rule: Rule}`, `EndInteraction`, `Skipped{rule, why}` (for the decisions log). `Policy::state() -> PolicyState` (for Status: idle / active since / passive since / stopped, shots so far, next check at).
- [ ] Tests (one or more per rule, as timelines): R1 first input after ≥2 s idle → start + shot of all screens; R2 settle shot 2 s after last input; R3 shot at least every 10 s in a continuous burst; R4 only changed screens, all-unchanged shot skipped (logged `Skipped`), R1 includes all; R5 end when no input within 2 s after the settle shot; R6 idle check every 45 s → passive shot of changed screens only; R7 one passive interaction across checks, ends on input (then R1 active), after 3 quiet checks, on lock; never mixes active and passive shots; R8 pause/lock/sleep end an open interaction and stop checks until resume (Review Focus 4). Change: clock digit, cursor, pointer → unchanged; a new message line, a scroll → changed.
- [ ] `cargo test -p vitality-client policy` → pass. Commit `desktop: the capture policy`.

### Task 4: `activity` and `session`

**Files:** `src-tauri/src/activity.rs` (evdev: open every `/dev/input/event*` that reports keys or relative/absolute pointer axes, re-scan on hotplug via a periodic re-scan every 30 s; emit an `Input` timestamp per event, coalesced to at most 10 per second), `src-tauri/src/session.rs` (zbus: logind session `LockedHint` changes and manager `PrepareForSleep`; emit `Locked(bool)`, `Sleeping(bool)`).

**Interfaces — produces:** `trait InputSource { fn subscribe(&self) -> mpsc::Receiver<Instant>; fn accessible(&self) -> Access }` (`Access::{Ok, NoDevices, PermissionDenied}`); `trait SessionWatch { fn subscribe(&self) -> mpsc::Receiver<SessionEvent> }`; real and fake implementations.
- [ ] Unit tests with fakes (coalescing, permission-denied reported as `PermissionDenied`, hotplug re-scan picks up a new device path via an injected directory listing). Commit `desktop: input activity and the session's lock and sleep`.

### Task 5: `screens`

**Files:** `src-tauri/src/screens/{mod.rs,portal.rs,feed.rs,registry.rs}`.

**Interfaces — produces:**
- `registry`: `ScreenEntry { id: ScreenId, name: String, restore_token: Option<String> }` persisted in `screens.json` (ordered list); `add`, `rename`, `remove`, `set_token`.
- `portal`: `trait Portal { async fn open(&self, token: Option<&str>) -> Result<Opened, PortalError> }` (`Opened { node_id, fd, restore_token }`), real impl with `ashpd` ScreenCast: `SourceType::Monitor`, `multiple=false`, `PersistMode::ExplicitlyRevoked`, cursor mode embedded; one session per screen.
- `feed`: one GStreamer pipeline per screen `pipewiresrc fd=… path=… always-copy=true keepalive-time=1000 ! video/x-raw,max-framerate=2/1,format={BGRx,RGBx,xRGB,xBGR,BGRA,RGBA,ARGB,ABGR,BGR,RGB} ! appsink max-buffers=1 drop=true sync=false`; on each sample convert to `RgbImage` and replace the screen's `Latest { image, at: Instant }`.
- `Screens` service: `open_all()` (each entry from its token; a refusal marks only that entry `NeedsPick`, Review Focus 1), `add()` (portal picker, new entry named `Screen N`), `repick(id)`, `remove(id)`, `latest(id) -> Option<Latest>`, `statuses() -> Vec<ScreenStatus { id, name, state: Live|NeedsPick|Opening, last_frame_age }>`, `preview(id) -> Option<Vec<u8>>` (JPEG ~480 px wide for the UI), `duplicates() -> Vec<(ScreenId, ScreenId)>` (thumbs within the change threshold continuously for 10 s).
- [ ] Tests with a fake `Portal` and a fake frame source: refusal of one of two screens; duplicates after 10 s; names default `Screen N`; registry round-trip; one screen → no `screen_name` emitted (asserted in Task 6). Commit `desktop: screens`.

### Task 6: `store` and `upload`

**Files:** `src-tauri/src/store.rs`, `src-tauri/src/upload.rs`.

**Interfaces — produces:**
- `Store::begin(start: DateTime<Utc>, local offset, tz) -> InteractionHandle` (folder name per layout rules); `handle.add_shot(screen_name: Option<&str>, image: &RgbImage, ts, trigger) -> String` (WebP at the quality setting, default 85; file name per layout rules); `handle.finish()` writes `interaction.json` (fields per `messages.py`, `source: "screen"`, `local_start`, `tz` IANA name from `/etc/localtime` or `TZ`); `Store::finish_interrupted()` at start (Review Focus 2); `Store::pending() -> Vec<PathBuf>` (complete, no `uploaded`/`failed`), `today() -> TodayCounts { shots, interactions }`, `disk_bytes()`, `free_up_space() -> Freed` (deletes only folders with `uploaded`).
- `Uploader` (tokio task): uploads `pending()` oldest first, status handling per Global Constraints, writes markers, exposes `UploadState { state: Ok|Offline|Rejected{device}|Disabled, pending, last_upload }`; wakes on a new finished interaction and on network-change (simple: retry timer).
- [ ] Tests against an in-process fake server (axum or hyper): 201/200 → `uploaded`; 409/422 → `failed`; 403 → pending + `Rejected`; offline for N attempts then success → all uploaded in order, none twice (Review Focus 3); interrupted folder finished and uploaded; Free up space spares pending. The multipart body is checked field by field against the protocol (part names, filenames, JSON that the Python `Interaction` model would accept — validate by running `uv run python -c` against `protocol/` in the test via a fixture file the test writes). Commit `desktop: storage and upload`.

### Task 7: Runtime, control socket, tray icon, Screens page

**Files:** `src-tauri/src/{runtime.rs,control.rs,tray.rs,commands.rs,log.rs,state.rs}`; `ui/src/{App.tsx,pages/Screens.tsx,components/*}`.

**Interfaces — produces:** `Runtime` owns `Policy`, `Screens`, `Store`, `Uploader`, input and session streams; a 250 ms tick calls `policy.on_tick` with the current thumbs and executes decisions (start/shot/end/skip), appending each decision to `capture.log`; pause state persisted in `state.json` (`paused_until`). Control socket: JSON lines `{"cmd":"status"|"pause","minutes"?|"resume"}` → `{"ok":true,...}`; CLI `vitality-client status|pause [MINUTES]|resume` prints a short human summary (or "Vitality isn't running"). Tray: icon states recording / paused / attention; left click toggles pause; menu: state line, Pause 30 min / 1 h / until tomorrow (until 06:00 local next day), Open Vitality, Quit. Closing the window hides it. Tauri commands: `screens_list`, `screens_add`, `screens_repick`, `screens_rename`, `screens_remove`, `screen_preview`, `pause`, `resume`, `status`; events: `state-changed`.
- [ ] Screens page per §5 (cards, live previews polled 1/s while visible, rename inline, Re-pick, Remove, Add screen, duplicate banner, "Needs to be picked again", "last frame N s ago"), in the theme.
- [ ] Tests: runtime with fakes end to end (input → interaction folder with shots → uploaded marker); pause mid-interaction ends it and stops capture (Review Focus 4); control socket round-trip; UI: `npm run typecheck`, `npm run build`, Playwright screenshots of the Screens page with mocked IPC (two screens, a duplicate warning, a needs-pick card) into the scratchpad, looked at. Commit `desktop: the runtime, tray icon and Screens page`.

## Milestone 2 — Status

### Task 8: Status page and tray state line

**Files:** `ui/src/pages/Status.tsx`; `commands.rs` (`status` gains `today`, `upload`, `disk`, `checks`, `recent_decisions` (last 50 from `capture.log`)).
- [ ] Status per §7 (live state in rule terms, today's shots/interactions, upload state and pending, disk, warnings, recent decisions incl. skipped); tray state line "Recording · N shots today" / "Paused until HH:MM" / "Needs attention: …". Playwright screenshots (recording, paused, offline, rejected). Commit `desktop: Status`.

## Milestone 3 — Settings, setup, health

### Task 9: `settings`, `health`, Setup, Settings and How capture works pages, `doctor`

**Files:** `src-tauri/src/{settings.rs,health.rs}`; `ui/src/pages/{Settings.tsx,Setup.tsx,HowItWorks.tsx}`.

**Interfaces — produces:** `Settings` (TOML, all policy numbers, `server`, `webp_quality`, `disk_warning_gb`, `start_at_login`) loaded/saved atomically; `start_at_login` applied with `systemctl --user enable|disable vitality-client.service` (reported if systemd is missing). `health::checks() -> Vec<Check { id, ok, detail, fix }>`: `input_access`, `desktop_session` (`WAYLAND_DISPLAY` or `DISPLAY` and `XDG_SESSION_TYPE`), `portal_available` (D-Bus name and ScreenCast interface present), `screens_open`, `server` in three parts: Tailscale running (`tailscale status --json`, `BackendState == "Running"`, which also gives this device's name), `GET /health` answers 200, and this device is accepted (a `POST /interactions` with an empty multipart body: 403 = not accepted, show the device name to allow on the server; 411/422 = accepted; nothing is stored either way), `disk_space`, `start_at_login`. `doctor` prints them with ✓/✗ and fixes; exit 1 if any essential fails.
- [ ] Setup per §8 (steps tick live; Copy button for the usermod command; Test for the server with each part reported; Screens step reuses the Screens page; start-at-login switch; Done); Setup shown whenever an essential check fails or no screen exists (Review Focus 5). Settings per §7; How capture works renders R1–R8 with current numbers (text from one Rust source of truth exposed by a command, not duplicated in TS).
- [ ] Tests: settings round-trip and validation (no negative or zero durations); each check good/bad with fakes; `doctor` output; Playwright screenshots of every Setup step and Settings. Commit `desktop: settings, setup and health`.

## Milestone 4 — packaging

### Task 10: PKGBUILD and docs

**Files:** `desktop/packaging/arch/PKGBUILD` (builds with `cargo tauri build --no-bundle` or `cargo build --release` + the UI build; installs `/usr/bin/vitality-client`, `/usr/lib/systemd/user/vitality-client.service`, `/usr/share/applications/vitality-client.desktop`, icons; `depends=(webkit2gtk-4.1 libayatana-appindicator gst-plugins-base gst-plugin-pipewire xdg-desktop-portal libwebp)`), `desktop/README.md` (run from source with `cargo tauri dev`, install with `makepkg -si`, where data lives, the rules, `doctor`), top-level README line, `client/README.md` note that it is superseded.
- [ ] `namcap`-clean if available; build verified on the hub as far as possible (the real `makepkg` runs on the owner's Arch machines during the move). Commit `desktop: packaging`.

## After the plan: moving the machines over (controller, over SSH)

Per §9, by the controller (no subagent needed): on the desktop, then the laptop — stop and remove the old client and Waybar module (restore the Waybar config backup), mark old capture folders `uploaded`, `makepkg -si`, start; the owner only picks the screens; then verify per §12's real-hardware list. Deploy Task 1's server and observer to the VM first.
