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
`desktop/ui`; `python3 ui/tests/ui_test.py [SCREENSHOT_DIR]` (needs node on PATH and the Python
`playwright` package with Chromium) checks the interface against the fake core: when Setup is
shown, Done, Skip, Settings' errors. The interface also runs in a browser against a fake core: `npm run dev` in
`desktop/ui`, then <http://localhost:5173/mock.html?scenario=two> (`duplicate`, `needs-pick`,
`empty`, `paused`).

## The parts

The core (`src-tauri/src/`): `policy` (rules R1–R9, pure), `activity` (`/dev/input`),
`screens` (portal screen-shares, frames, names), `store` (interaction
folders), `upload`, `runtime` (wires them), `control` (the CLI's socket), `tray`, `commands` (for
the interface). Each file starts with its contract.

## The command line

`vitality-client` without arguments is the app. With arguments: `status`, `pause [MINUTES]`,
`resume` (through the running app's socket) and `doctor`: every health check (input access, the
desktop session, the screen-share portal, the screens, Tailscale, the server and whether it
accepts this device, disk space, start at login) with ✓/✗ and a fix for each failure; it exits 1
when an essential one fails. While the app runs, `doctor` prints the app's own results (its
session, not the terminal's: it works over SSH); otherwise it runs them in the terminal. The same
checks drive Setup (on the first run, and later while an essential check fails) and the warnings
on Status and the tray.

## Settings

`~/.config/vitality/client.toml`, written by the Settings page: the server, every number of the
rules (How capture works shows them in words), image quality, the disk warning level and start at
login (`systemctl --user enable|disable vitality-client.service`).

## Setup notes for some desktops

- **Saved screen permissions (Hyprland, xdg-desktop-portal-hyprland):** tick "Allow a restore
  token" in the share picker, or set `screencopy { allow_token_by_default = true }` in
  `~/.config/hypr/xdph.conf`. Without a token, a screen needs a new pick after each login (the app
  never opens the picker by itself at start).
- **Lock and sleep** are not watched: capture goes on while the screen is locked (pause it if
  you want it to stop). After a system sleep, an interaction left open ends at once, with no
  shot for the gap.
- **Tray:** the icon appears whenever a tray host (e.g. Waybar's tray) starts, also after the app.
- **WebKit on Wayland/NVIDIA:** the app sets `WEBKIT_DISABLE_DMABUF_RENDERER=1` unless it is
  already set (the DMA-BUF renderer crashed with "Error 71 (Protocol error)").
