# vitality-client (desktop)

Vitality's capture client for Linux desktops: one app with a tray icon and a window, a Rust core
(`src-tauri/`) and a React interface (`ui/`), built with Tauri. It captures the screens you pick
while you use the keyboard and mouse (and, while you are idle, when a screen changes), stores
each interaction as a folder of WebP shots and an `interaction.json`, and uploads it to your
Vitality server (its address is set in Setup or Settings). Design:
`docs/superpowers/specs/2026-11-07-desktop-client-design.md`.

## Install (Arch)

```sh
cd desktop/packaging/arch
makepkg -si
```

The PKGBUILD builds from this checkout as it is on disk (a dirty or detached worktree is fine;
only crates and npm packages are downloaded) and installs `/usr/bin/vitality-client`, the systemd
user unit, the launcher entry and the icons. Then start it once (it starts by itself at later
logins when Start at login is on):

```sh
systemctl --user enable --now vitality-client
```

On **Hyprland** (or any session that does not start `graphical-session.target`, i.e. without
uwsm), add to `~/.config/hypr/hyprland.conf`:

```
exec-once = systemctl --user start vitality-client
```

The Start at login check in Setup, Status and `doctor` warns when this is missing.

Uninstall: `systemctl --user disable --now vitality-client && sudo pacman -R vitality-client`.
Your captures, settings and screens (below) stay; delete them by hand if you want.

## Where things live

| What | Where |
|---|---|
| Captures, `uploaded` / `failed` markers | `~/.local/share/vitality/client/captures/<day>/<time>/` |
| Settings | `~/.config/vitality/client.toml` (written by the Settings page) |
| Screens and their saved permissions | `~/.local/state/vitality/client/screens.json` |
| Pause, setup done | `~/.local/state/vitality/client/state.json` |
| Recent decisions | `~/.local/state/vitality/client/capture.log` (the last 500) |
| Control socket | `$XDG_RUNTIME_DIR/vitality/client.sock` (while the app runs) |
| App log | `journalctl --user -u vitality-client` |

Frames and previews exist only in memory. Nothing is deleted automatically; Settings has
**Free up space**, which deletes the local copies the server already has.

## How capture works

Every shot is a shot of every screen; the numbers are settings (How capture works in the app
shows them in words).

- **Start:** any keyboard or mouse input while no interaction is open starts one, with a shot.
- **While you're busy:** while active, a shot at least every 10 s.
- **End:** after 3 s without input, a closing shot, and the interaction ends and is uploaded.
- **While you're idle:** while idle, every 45 s, each screen is compared with its last shot; if one
  changed (a video, a call), a passive shot. These form one passive interaction, which ends on
  input or after 3 checks in a row without change.
- **Pause:** while paused, nothing is captured; an open interaction ends.
- **Size limit:** at 40 MiB (or 900 shots) an interaction ends and a new one starts at once.

Lock and sleep are not watched: the lock screen is captured like any screen (pause if you want
it not to be). After a sleep, an interaction left open ends at once, with no shot for the gap.

## The command line

`vitality-client` without arguments is the app (a second start shows the running one's window).
With arguments:

- `vitality-client status`: what it is doing, the screens, input, upload;
- `vitality-client pause [MINUTES]` (without minutes: until resumed) and `resume`;
- `vitality-client doctor`: every health check (input access, the desktop session, the
  screen-share portal, the screens, Tailscale, the server and whether it accepts this device,
  disk space, start at login) with ✓/✗ and a fix for each failure; exit 1 when an essential one
  fails. While the app runs these are its own results (its session, so it works over SSH);
  otherwise it runs them in the terminal and says so.

The same checks drive Setup (on the first run, and later while an essential check fails) and the
warnings on Status and in the tray.

## Notes for some desktops

- **Saved screen permissions:** when the share picker offers it, tick **Allow a restore token**
  (on Hyprland, or set `screencopy { allow_token_by_default = true }` in
  `~/.config/hypr/xdph.conf`). Without a token a screen needs a new pick after each login; the
  app never opens the picker by itself at start.
- **Input:** Vitality reads `/dev/input`, which needs the `input` group:
  `sudo usermod -aG input $USER`, then log out and in. The app never runs sudo.
- **Tray:** the icon (StatusNotifierItem) appears whenever a tray host starts, also after the app
  (e.g. Waybar's tray). Without one, open the window from the launcher.
- **WebKit on Wayland/NVIDIA:** the app sets `WEBKIT_DISABLE_DMABUF_RENDERER=1` unless it is
  already set (WebKit's DMA-BUF renderer crashed with "Error 71 (Protocol error) dispatching to
  Wayland display").

## Troubleshooting

`journalctl --user -u vitality-client -f` shows what the app says (screens that need a pick,
uploads refused, settings it could not read); `vitality-client doctor` says what is missing;
the Status page lists the recent decisions.

## Build and run from source

Arch: `pacman -S rust nodejs npm webkit2gtk-4.1 gstreamer gst-plugins-base gst-plugin-pipewire`.
Ubuntu: `apt install libwebkit2gtk-4.1-dev librsvg2-dev libgstreamer1.0-dev
libgstreamer-plugins-base1.0-dev libudev-dev libssl-dev pkg-config gstreamer1.0-pipewire`, plus
Rust (stable) and Node.

```sh
cd desktop/ui && npm ci
npm run tauri dev                    # the app, the interface hot-reloading (cargo tauri dev)
npm run tauri build -- --no-bundle   # desktop/target/release/vitality-client
npm run tauri build -- --bundles deb # optional: a .deb under desktop/target/release/bundle/
```

The interface also runs in a browser against a fake core: `npm run dev` in `desktop/ui`, then
<http://localhost:5173/mock.html?scenario=two> (also `duplicate`, `needs-pick`, `stopped`,
`setup`, `health-fail`, `paused`, `offline`, …; `&page=status|screens|settings|how`).

Tests: `cargo test` and `cargo clippy -- -D warnings` in `desktop/`; `npm run typecheck` in
`desktop/ui`; `python3 ui/tests/ui_test.py [SCREENSHOT_DIR]` (needs node on PATH and the Python
`playwright` package with Chromium) checks the interface against the fake core.

The core (`src-tauri/src/`): `policy` (the capture rules, pure), `activity` (`/dev/input`), `screens`
(portal screen-shares, frames, names), `store` (interaction folders), `upload`, `runtime` (wires
them), `health` (the checks), `settings`, `control` (the CLI's socket), `tray`, `commands` (for
the interface). Each file starts with its contract.
