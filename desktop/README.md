# vitality-client (desktop)

Vitality's capture client for Linux desktops: one Tauri app with a Rust core (`src-tauri/`) and a
React interface (`ui/`). It captures the owner's screens around keyboard and mouse activity, and
while idle when a screen changes, stores each interaction as a folder of WebP shots and an
`interaction.json`, and uploads it to the Vitality server. Design:
`docs/superpowers/specs/2026-11-07-desktop-client-design.md`.

## Build and run from source

System packages (Ubuntu names; Arch: `webkit2gtk-4.1 gstreamer gst-plugins-base
gst-plugin-pipewire libwebp xdg-desktop-portal`). The tray icon is drawn by the app itself over
D-Bus (StatusNotifierItem, the `ksni` crate), so libayatana-appindicator is not needed at run time:

```sh
sudo apt install libwebkit2gtk-4.1-dev libayatana-appindicator3-dev librsvg2-dev \
  libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev libwebp-dev libudev-dev libssl-dev pkg-config
```

Then, with Rust (stable) and Node:

```sh
cd desktop/ui && npm install
npm run tauri dev                    # the app, with the interface hot-reloading
npm run tauri build -- --no-bundle   # target/release/vitality-client
```

Tests: `cargo test` and `cargo clippy -- -D warnings` in `desktop/`; `npm run typecheck` in
`desktop/ui`. The interface also runs in a browser against a fake core: `npm run dev` in
`desktop/ui`, then <http://localhost:5173/mock.html?scenario=two> (`duplicate`, `needs-pick`,
`empty`, `paused`).

## The parts

The core (`src-tauri/src/`): `policy` (rules R1–R8, pure), `activity` (`/dev/input`), `session`
(logind lock and sleep), `screens` (portal screen-shares, frames, names), `store` (interaction
folders), `upload`, `runtime` (wires them), `control` (the CLI's socket), `tray`, `commands` (for
the interface). Each file starts with its contract.

## The command line

`vitality-client` without arguments is the app. With arguments: `status`, `pause [MINUTES]`,
`resume` (through the running app's socket) and `doctor`.

## Setup notes for some desktops

- **Saved screen permissions (Hyprland, xdg-desktop-portal-hyprland):** tick "Allow a restore
  token" in the share picker, or set `screencopy { allow_token_by_default = true }` in
  `~/.config/hypr/xdph.conf`. Without a token, a screen needs a new pick after each login (the app
  never opens the picker by itself at start).
- **Lock:** capture stops on logind's `Lock` signal or `LockedHint`, or an active
  `org.freedesktop.ScreenSaver`. With hypridle, `lock_cmd = loginctl lock-session` sends the
  first; while locked the app checks `LockedHint` every 5 s and resumes when it is false and no
  screensaver is active. Each change is written to `capture.log` with the signal behind it.
- **Tray:** the icon appears whenever a tray host (e.g. Waybar's tray) starts, also after the app.
- **WebKit on Wayland/NVIDIA:** the app sets `WEBKIT_DISABLE_DMABUF_RENDERER=1` unless it is
  already set (the DMA-BUF renderer crashed with "Error 71 (Protocol error)").
