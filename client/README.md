# vitality-client

A small Wayland daemon that takes a "before" and an "after" screenshot around each burst of
keyboard or mouse activity and stores them locally (WebP files plus a SQLite index).
Nothing leaves the machine. Wayland only.

## Install

1. System packages and permissions:

   ```sh
   sudo pacman -S python-gobject gst-plugins-base gst-plugin-pipewire xdg-desktop-portal-hyprland
   sudo usermod -aG input $USER              # then log out and back in
   ```

   (`xdg-desktop-portal-hyprland` is the ScreenCast backend on Hyprland; use your compositor's
   backend elsewhere, and the distro equivalents of the other packages.)

   The `input` group is how the daemon reads `/dev/input` to notice activity. See
   [Security note](#security-note).

2. In this directory (`client/`), create the virtualenv with system site packages, so the
   system PyGObject/GStreamer are importable, and install the project:

   ```sh
   uv venv --python /usr/bin/python3 --system-site-packages
   uv sync
   ```

3. Put both commands on your `PATH`: `vitality-clientd` is the daemon (the service unit
   expects `~/.local/bin/vitality-clientd`), `vitality-client` controls it.

   ```sh
   mkdir -p ~/.local/bin
   ln -s "$PWD/.venv/bin/vitality-clientd" ~/.local/bin/vitality-clientd
   ln -s "$PWD/.venv/bin/vitality-client" ~/.local/bin/vitality-client
   ```

   `uv tool install --editable .` is not an option: it builds an isolated environment
   without system site packages, so GStreamer cannot be imported there.

4. Check the setup:

   ```sh
   vitality-client doctor
   ```

   It lists what is missing and how to fix it, then prints the config, data and state paths.

5. Run the daemon as a user service, started with your graphical session:

   ```sh
   systemctl --user link "$PWD/systemd/vitality-client.service"
   systemctl --user enable --now vitality-client
   journalctl --user -u vitality-client -f     # logs
   ```

   The unit is tied to `graphical-session.target`, which only some setups start (for
   example Hyprland through uwsm). Check yours:

   ```sh
   systemctl --user is-active graphical-session.target
   ```

   If it prints `inactive`, `enable` alone will not start the service at login. Start it
   from the compositor instead; for Hyprland add this to `hyprland.conf`, after any
   environment-import line (`systemctl --user enable` is then optional):

   ```
   exec-once = dbus-update-activation-environment --systemd WAYLAND_DISPLAY XDG_CURRENT_DESKTOP XDG_SESSION_TYPE && systemctl --user start vitality-client.service
   ```

   The service needs the compositor's environment (`WAYLAND_DISPLAY`, `XDG_SESSION_TYPE`) in
   the systemd user manager; the `&&` makes sure the import finishes before the start. If the
   service reports that it requires a Wayland session, that import did not happen.

6. Approve screen capture. On its first start the daemon asks for screen-sharing permission,
   so the portal's share picker appears.

   On Hyprland, in the picker select a **screen/monitor** (not a window or a region) and
   tick **"Allow a restore token"**. Otherwise the portal returns no token and the dialog
   appears on every start. Alternative: add this to `~/.config/hypr/xdph.conf` so a token is
   always issued:

   ```
   screencopy {
       allow_token_by_default = true
   }
   ```

   Once a token is saved (in the state file), later starts reuse it without a dialog. If the
   token is lost or rejected, the picker appears again.

7. Check that it works: `vitality-client status` should show `activity: available` and
   `capture: available`, and `vitality-client capture` saves a screenshot and prints its
   path.

   If it shows `activity: unavailable` right after you added yourself to the `input` group,
   reboot: the systemd user manager may still have the old groups, so logging out and back
   in is not always enough.

   To check CPU use, scroll or type for a while and watch the daemon:

   ```sh
   top -p $(pgrep -f vitality-clientd)
   ```

## Commands

`vitality-clientd` is the daemon itself (what the service runs). Everything else goes
through `vitality-client`, which talks to the running daemon:

| Command | Does |
|---|---|
| `vitality-client pause [DURATION]` | Pause, optionally for e.g. `30s`, `30m`, `2h` |
| `vitality-client resume` | Resume |
| `vitality-client toggle` | Pause if capturing, resume if paused (for a bar button or keybind) |
| `vitality-client status` | State, captures today, last capture, disk use, warnings, paths |
| `vitality-client status --json` | The same as one line of JSON |
| `vitality-client doctor` | Check the setup |
| `vitality-client capture` | Take a screenshot now (even while paused) and print its path |
| `vitality-client reset [--all] [--yes]` | Delete all captures and the index (asks first; the daemon must be stopped). `--all` also deletes state, so the screen-sharing picker appears again |

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
    "interval": 5,
    "on-click": "vitality-client toggle; pkill -SIGRTMIN+9 waybar",
    "signal": 9
}
```

Clicking toggles pause; the signal refreshes the module immediately.

## Development

```bash
uv run pytest     # tests
uv run pyright    # type check: implementations must match their interfaces
```

Layout: `daemon/` is `vitality-clientd` and the parts only it uses (policy, activity,
frames, storage); `cli/` is `vitality-client`; `shared/` holds what both use: `config.py`
(paths and settings), `state.py` (saved pause state) and `channel.py` (the socket
protocol). Neither `daemon/` nor `cli/` imports the other.

Each package's `interface.py` holds its contract (`Protocol`s plus the data types they use);
the implementations inherit from it and pyright checks they match. Start reading at a
package's `__init__.py`; `daemon/daemon.py` opens with the daemon's own contract.

## Known limitations

- On Hyprland the portal's picker returns a single monitor, so only that monitor is
  captured; other monitors do not appear in the screenshots.
- After undocking (that output disappearing) the saved permission no longer matches, so the
  picker will appear again.

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
