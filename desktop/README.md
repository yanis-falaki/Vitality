# vitality-client (desktop)

Vitality's capture client for Linux desktops: one Tauri app with a Rust core (`src-tauri/`) and a
React interface (`ui/`). It captures the owner's screens around keyboard and mouse activity, and
while idle when a screen changes, stores each interaction as a folder of WebP shots and an
`interaction.json`, and uploads it to the Vitality server. Design:
`docs/superpowers/specs/2026-11-07-desktop-client-design.md`.

## Build and run from source

System packages (Ubuntu names; Arch: `webkit2gtk-4.1 libayatana-appindicator gstreamer
gst-plugins-base gst-plugin-pipewire libwebp`):

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
`desktop/ui`.

## The command line

`vitality-client` without arguments is the app. With arguments: `status`, `pause [MINUTES]`,
`resume` (through the running app's socket) and `doctor`.
