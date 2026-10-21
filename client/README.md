# vitality-client

A small Wayland daemon that takes a "before" and an "after" screenshot around each burst of
keyboard or mouse activity and stores them locally (WebP files plus a SQLite index).
Nothing leaves the machine. Wayland only.

## Install

1. System packages and permissions:

   ```sh
   sudo pacman -S gst-plugin-pipewire        # or the distro equivalent
   sudo usermod -aG input $USER              # then log out and back in
   ```

   The `input` group is how the daemon reads `/dev/input` to notice activity. See
   [Security note](#security-note).

2. In this directory (`client/`), create the virtualenv with system site packages, so the
   system PyGObject/GStreamer are importable, and install the project:

   ```sh
   uv venv --python /usr/bin/python3 --system-site-packages
   uv sync
   ```

3. Put the command on your `PATH` (the service unit expects `~/.local/bin/vitality-client`):

   ```sh
   mkdir -p ~/.local/bin
   ln -s "$PWD/.venv/bin/vitality-client" ~/.local/bin/vitality-client
   ```

   `uv tool install --editable .` is not an option: it builds an isolated environment
   without system site packages, so GStreamer cannot be imported there.

4. Check the setup:

   ```sh
   vitality-client doctor
   ```

   It lists what is missing and how to fix it, then prints the config, data and state paths.

5. Approve screen capture once. This saves one capture and shows the portal's permission
   dialog the first time; approve it:

   ```sh
   vitality-client capture-once
   ```

   The permission is remembered, so the service will not ask again.

6. Run it as a user service, started with your graphical session:

   ```sh
   systemctl --user link "$PWD/systemd/vitality-client.service"
   systemctl --user enable --now vitality-client
   journalctl --user -u vitality-client -f     # logs
   ```

   If the service reports that it requires a Wayland session, your compositor has not
   exported its environment to systemd; for example, in Hyprland's config:
   `exec-once = dbus-update-activation-environment --systemd WAYLAND_DISPLAY XDG_CURRENT_DESKTOP XDG_SESSION_TYPE`.

## Commands

| Command | Does |
|---|---|
| `vitality-client run` | Run the daemon (what the service does) |
| `vitality-client pause [DURATION]` | Pause, optionally for e.g. `30s`, `30m`, `2h` |
| `vitality-client resume` | Resume |
| `vitality-client status` | State, captures today, last capture, disk use, warnings, paths |
| `vitality-client status --json` | The same as one line of JSON |
| `vitality-client doctor` | Check the setup |
| `vitality-client capture-once` | Save one capture now and print its path |

Pausing takes one final capture if an interaction is in progress, so pause before sensitive
content appears, not after.

### Pause keybinding (Hyprland)

```
bind = SUPER, F12, exec, vitality-client pause
bind = SUPER SHIFT, F12, exec, vitality-client resume
```

### Status bar (Waybar)

`status --json` prints one line with `state` (`idle`, `active` or `paused`),
`captures_today`, `disk_warning` and more, and exits 1 with no output on stdout when the
daemon is not running:

```json
"custom/vitality": {
    "exec": "vitality-client status --json | jq -r 'if .state == \"paused\" then \"paused\" else \"rec \\(.captures_today)\" end'",
    "interval": 5
}
```

## Configuration

Optional `~/.config/vitality/client.toml` (defaults shown):

```toml
settle_seconds = 2     # quiet time after the last input before the "after" capture
max_gap_seconds = 10   # longest an interaction can run before it is split
webp_quality = 85
disk_warning_gb = 20   # status and the log warn above this; nothing is deleted
```

Files follow the XDG convention; `status` and `doctor` print the exact paths.

| What | Default location |
|---|---|
| Config | `~/.config/vitality/client.toml` |
| Captures and `index.sqlite` | `~/.local/share/vitality/client/` |
| Pause state, portal restore token | `~/.local/state/vitality/client/state.json` |
| Control socket | `$XDG_RUNTIME_DIR/vitality/client.sock` |

## Security note

Membership in the `input` group lets every process of your user read all input devices,
which weakens Wayland's protection against keylogging. This is accepted for v0 on a
single-user machine you own. The input watcher reduces each event to a timestamp before it
leaves its module, so it can later be split into a separate process running as a dedicated
system user; that split is required before this runs on anyone else's machine. Logs never
contain key data, pointer data or image contents.
