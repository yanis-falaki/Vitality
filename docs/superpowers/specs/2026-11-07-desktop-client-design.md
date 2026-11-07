# Vitality — Desktop client design

**Date:** 2026-11-07
**Status:** Agreed with the owner section by section; for the owner's review.

## 1. Purpose

A new capture client for Linux desktops, built from the ground up: one self-contained app that the
owner can see, understand and control. It replaces the headless `client/` daemon, its CLI and its
Waybar module. It captures the owner's screens as evidence for Vitality, and uploads it to the
server in the existing format.

Success: setting up several screens takes one pass and is visibly right; pausing is one click; no
silent duplicate or frozen screens; nothing is specific to one desktop environment; the owner can
see at any time what it is doing and why.

## 2. Scope

**In:** the app (tray icon, window, first-run setup, settings, status), the capture policy, screens
set up and named by the owner, passive capture, local storage and upload, a small CLI, a local Arch
package, the two protocol additions with the server and observer updates.

**Out:** macOS, Windows, phones (ports later, if ever); publishing packages (AUR, PPA, Flathub);
Flatpak; a self-updater; the web app, the modeler; multi-device features; migration code (moving the
owner's two machines over is a manual checklist, §9).

**Built in milestones, each a working app:** (1) the shell: core, capture, tray icon, Screens page,
upload; (2) Status; (3) Settings and first-run setup; (4) packaging; then the owner's two machines are moved over by hand.

## 3. Decisions

| Question | Decision |
|---|---|
| Shape | One app (Tauri): a Rust core and a web interface, one process; capture runs inside it |
| Lifecycle | A systemd user service starts it at login (a setting, on by default) and restarts it after a crash; closing the window hides it to the tray; Quit stops capture |
| Old client | Not a basis for the design. Replaced, with the Waybar module |
| Platforms | Linux first: GNOME, KDE, Hyprland, Sway; portable APIs only (the screen-share portal, the standard tray icon protocol) |
| Upload format | The existing one (an interaction folder + `interaction.json`), with two additions (§9) |
| Screens | Set up by the owner: one portal screen-share per screen, each named by the owner |
| Capture | Activity-driven, plus a passive check while idle; explicit rules R1–R8 (§6) |
| Storage | Folders only, no database; an `uploaded` marker per interaction |
| CLI | `vitality-client status · pause · resume · doctor`, through the app's socket (except `doctor`) |
| Packaging | A local `PKGBUILD` in the repo (`makepkg -si`); run from source while building |

## 4. The app

One program, `vitality-client`, in a new `desktop/` folder (Tauri). Without arguments it is the app;
with arguments it is the CLI.

**The core (Rust)**, in small modules, each with its contract at the top:

| Module | Does |
|---|---|
| `screens` | the portal screen-shares, one per screen, reopened from saved permissions; frames copied out of PipeWire's buffers on arrival, the newest per screen kept in memory; the owner's names |
| `activity` | keyboard and mouse activity from Linux input devices (`/dev/input`, needs the `input` group) |
| `policy` | rules R1–R8 as a pure state machine: given time, input and screen changes, what to capture; no I/O |
| `store` | interactions on disk: screenshots (WebP) as they are taken, `interaction.json` at the end |
| `upload` | finished interactions to the server, retried until confirmed |
| `control` | the socket for the CLI |
| `health` | the checks behind setup, Status and `doctor` (§8) |

**The interface (web: React + TypeScript)**, in the Vitality web app's theme (same fonts, colours,
calm spacing; light or dark following the desktop), talking to the core through Tauri's typed
messages.

## 5. Screens

A screen is one portal screen-share plus the owner's name for it. The screens list (order, names,
each one's saved portal permission) is kept in `screens.json` (§10).

**Screens page:** one card per screen with a live preview (about once a second, only while the
window is open), its name (editable), "last frame N s ago", **Re-pick** and **Remove**. **Add
screen** opens the portal's picker; the new card shows its preview at once, named "Screen N" until
renamed. If two previews stay nearly identical for 10 s, a banner says they look like the same
monitor.

**At start** each screen reopens from its saved permission without a dialog. If the portal refuses
one, only its card says "Needs to be picked again"; the others keep capturing. No automatic
restarts: "last frame" ages show a stuck feed.

**Frames** are copied out of the PipeWire buffer as soon as they arrive and the buffer is released
at once (holding buffers starved Hyprland's portal and froze a screen); each feed is capped at 2
frames per second; normal memory is requested, GPU buffers only if nothing else is offered.

**One screen:** the name is optional and no `screen_name` is written.

## 6. The capture policy

Times are the defaults; every number is a setting.

- **R1 · Start.** Any keyboard or mouse input while no interaction is open starts an active
  interaction, with a shot of every screen.
- **R2 · End.** After 3 s without input, a closing shot (by R4's rule; if no screen changed there
  is no shot, but the interaction still ends), and the interaction ends at once and is uploaded.
- **R3 · Maximum gap.** While continuously active, a shot at least every 10 s.
- **R4 · Which screens.** A shot includes a screen only if it changed since that screen's own last
  shot: more than 0.5 % of a 64×36 grayscale thumbnail moved by more than 16 levels. A shot in which
  no screen changed is skipped. R1 shots include every screen.
- **R5 · Passive check.** While idle, every 45 s, each screen is compared with its last shot by
  R4's rule; if any changed, a shot of the changed screens, marked `passive`.
- **R6 · Passive interaction.** The first passive shot starts one; every passive shot until it ends
  belongs to it. It ends on any input (R1 then starts an active one), after 3 consecutive checks
  without change, or on pause. It holds only passive shots.
- **R7 · Stop.** While paused, nothing is captured and no checks run; an open interaction ends.
  (Lock and sleep are not watched: the owner does not need capture to stop on lock, and while the
  machine sleeps the app is frozen with it. An interaction left open across a sleep ends as soon as
  the machine wakes, with no shots for the gap; the input that wakes it starts a new one.)
- **R8 · Size budget.** When an open interaction's shots reach 40 MiB (or 900 shots), it ends and a
  new interaction of the same kind starts at once with a shot of every screen (active: as R1;
  passive: a passive start). Every interaction then stays under the server's 50 MiB upload limit
  (and its 1000-file limit). A 413 that still happens shows as a warning in Status.

(The rules were renumbered when settle and the start gap were merged into R2's single
`end_after_idle_s`; the old R5 is gone.)

## 7. Tray icon and window

**Tray icon** (StatusNotifierItem): filled dot while recording, hollow when paused, grey with a mark
when something needs attention. Left click toggles pause. Menu: the state line ("Recording · 214
shots today"), Pause for 30 min / 1 h / until tomorrow, Open Vitality, Quit.

**Window**, pages in a sidebar:
- **Status** — what it is doing now in the terms of the rules ("Active interaction since 8:02 p.m. ·
  14 shots", "Idle · passive check in 31 s", "Paused until 9 p.m."), today's shots and interactions,
  upload state and pending, disk use, warnings from the health checks, and the recent-decisions log
  (including skipped shots).
- **Screens** — §5.
- **Settings** — start at login; server address; the timings and threshold of the rules; image quality;
  the disk warning level; **Free up space** (deletes local copies already uploaded, after asking).
- **How capture works** — R1–R8 in plain words with the current numbers.
- **Setup** — §8, instead of the pages when something essential is missing.

## 8. First-run setup and health

Shown on first start and whenever an essential check fails; each step ticks as soon as it passes.

1. Welcome: what is recorded, where it goes, that it can be paused.
2. Input access: in the `input` group? If not, the command (`sudo usermod -aG input $USER`) with a
   Copy button, and "then log out and in". The app never runs `sudo`.
3. Server: address (default `http://vitality-librarian:8100`) and **Test**: Tailscale up, server
   answers, this device accepted — each reported separately, with the device name to allow on the
   server. Skippable: captures then stay local.
4. Screens: the Screens page, "Add your first screen".
5. Start at login: the switch, on.
6. Done: a summary; capture starts.

**One list of checks** serves setup, Status and `vitality-client doctor`: input access, a desktop
session, the portal available, every screen reopened, the server reachable and accepting this
device, disk space, start at login. Each check has a fix text, shown in the app and printed by
`doctor`.

## 9. Storage, upload and the rest of Vitality

**On the machine:** interactions in `captures/<day>/<time>/` (UTC names, as today): screenshots
written as taken, `interaction.json` at the end. A folder without `interaction.json` (crash, power
loss) is finished from its screenshots at the next start. After the server confirms, an empty
`uploaded` file marks the folder. Nothing is deleted automatically.

**Upload:** each interaction as soon as it ends, in the existing request format; failures retried
with growing waits up to 5 min and at once when the network returns; pending uploads survive
restarts (folders without the marker); a refused device is shown on Status with its device name.

**Protocol** (`protocol/`, shared with the server): `Shot.trigger` gains `passive`; `Shot` gains
optional `screen_name`. Nothing else. The `screen`, `screen_position` and `focused` fields of the
unmerged `screens` branch are not used.

**Server:** accepts the two additions. **Observer:** each image line names the screen when there is
one and marks passive shots ("Screenshot 3 (12 s) · Main · passive"); its prompt says images are
per screen, named by the owner, and that passive shots mean watching or reading.

**Moving over** (laptop and desktop) is a one-time, **manual** step, done by the assistant (or a subagent) over
SSH; the app has no migration code. Per machine: stop and remove the old client (its service, its install under
`~/.local/share/vitality-client`, its `index.sqlite`, the Hyprland start line) and the Waybar module
(restoring the Waybar config from its backup); mark the existing local capture folders as uploaded
(an empty `uploaded` file in each), or delete them, so the new app does not send them again; install
the app; go through setup once (server address, screens). Only picking the screens needs the
owner at the machine: the portal's picker asks for a click there.

## 10. Where things live

| What | Where |
|---|---|
| Captures, `uploaded` markers | `~/.local/share/vitality/client/captures/<day>/<time>/` |
| Settings | `~/.config/vitality/client.toml` (written by the Settings page) |
| Screens and their saved permissions | `~/.local/state/vitality/client/screens.json` |
| Pause state | `~/.local/state/vitality/client/state.json` |
| Recent-decisions log | `~/.local/state/vitality/client/capture.log` (last few hundred lines) |
| Control socket | `$XDG_RUNTIME_DIR/vitality/client.sock` (while the app runs) |
| App log | the systemd journal (`journalctl --user -u vitality-client`) |

Frames and previews exist only in memory. The `client` names are kept, so the folders sit where the
old client's did.

## 11. Packaging

One program `vitality-client`, plus a launcher entry with the icon and the systemd user service.
Installed on the owner's machines from a **local `PKGBUILD`** in `desktop/packaging/arch/`
(`makepkg -si`), managed by `pacman`. While building: run from source (`cargo tauri dev`). The build
may also produce a `.deb` and an AppImage; publishing anything is the owner's later decision. No
Flatpak, no self-updater.

## 12. Testing

- **Policy:** R1–R8 as timelines through the pure state machine, each rule with its own cases
  (incl. long bursts, idle screens skipped, a video while idle forming one passive interaction,
  pause, a sleep in the middle of an interaction).
- **Change detection:** clock digit, cursor, pointer → no change; a new message, scrolling → change.
- **Store and upload:** against a fake server in a temporary folder: markers, finishing interrupted
  folders, retries, refused device, Free up space deleting only uploaded ones.
- **Interface:** type checks; headless runs against a fake core (setup, screens with the duplicate
  warning, status, settings) with screenshots looked at.
- **Health and CLI:** each check against simulated good and bad machines; `status`, `pause`,
  `doctor` against a running app.
- **On the owner's desktop, then laptop:** two screens with correct previews and names, live feeds
  (the clock ticks in the previews), a video while idle giving a passive interaction, restart reopening screens without dialogs, uploads catching up after a network loss.
