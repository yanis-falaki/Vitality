# vitality-client

A small Wayland daemon that takes screenshots around each burst of keyboard or mouse
activity, groups them into one folder per interaction (WebP files plus an `interaction.json`
and a SQLite index), and can upload each finished interaction to a Vitality server over
Tailscale. Without `upload_server` set, nothing leaves the machine. Wayland only.

## Install

The daemon runs from its own installed copy under `~/.local/share/vitality-client`, built
from this repo by `client/install.sh`; editing the repo never changes the running daemon.

1. System packages and permissions:

   ```sh
   sudo pacman -S python-gobject gst-plugins-base gst-plugin-pipewire xdg-desktop-portal-hyprland
   sudo usermod -aG input $USER              # then log out and back in
   ```

   (`xdg-desktop-portal-hyprland` is the ScreenCast backend on Hyprland; use your compositor's
   backend elsewhere, and the distro equivalents of the other packages.) You also need
   [`uv`](https://docs.astral.sh/uv/).

   The `input` group is how the daemon reads `/dev/input` to notice activity. See
   [Security note](#security-note).

2. From the root of the repo, as your normal user (no sudo), run the installer:

   ```sh
   ./client/install.sh
   ```

   It builds a virtualenv with system site packages (so the system PyGObject/GStreamer are
   importable) in `~/.local/share/vitality-client/venv`, installs the locked dependencies and
   a copy of `client/` and `protocol/` into it, puts `vitality-client` and `vitality-clientd`
   in `~/.local/bin` (make sure that is on your `PATH`), and installs the user service
   `vitality-clientd.service`. It checks the system prerequisites first and says how to fix
   what is missing. Rerunning it is safe: it stops the daemon, replaces the program and
   starts it again.

3. Run the daemon as a user service, started with your graphical session. The unit is tied
   to `graphical-session.target`, which only some setups start (for example Hyprland through
   uwsm). Check yours:

   ```sh
   systemctl --user is-active graphical-session.target
   ```

   If it prints `inactive`, `enable` alone will not start the service at login. Start it
   from the compositor instead; for Hyprland add this to `hyprland.conf`, after any
   environment-import line:

   ```
   exec-once = dbus-update-activation-environment --systemd WAYLAND_DISPLAY XDG_CURRENT_DESKTOP XDG_SESSION_TYPE && systemctl --user start vitality-clientd.service
   ```

   The service needs the compositor's environment (`WAYLAND_DISPLAY`, `XDG_SESSION_TYPE`) in
   the systemd user manager; the `&&` makes sure the import finishes before the start. If the
   service reports that it requires a Wayland session, that import did not happen. Logs:
   `journalctl --user -u vitality-clientd -f`.

4. Check the setup:

   ```sh
   vitality-client doctor
   ```

   It lists what is missing and how to fix it, then prints the config, data and state paths.

5. Start the service now (`systemctl --user start vitality-clientd`) or log in again.

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

### Updating

Commit or merge your changes to `main` in this repo, then:

```sh
vitality-client upgrade
```

It checks that the repo is on `main` with no modified tracked files, lists the commits it
brings, runs `./client/install.sh` there (restarting the daemon), and finishes with a fresh
`vitality-client doctor`. With nothing new on `main` it says `already installed` and stops.
It does not fetch or pull: the repo is wherever `install.sh` was last run from.

### Upgrading from the old capture layout

Before interactions, captures were stored as `captures/<date>/<ULID>.webp` with a `captures`
table in `index.sqlite`. The current daemon refuses to start on that layout and exits with
`old capture layout found; run the one-time migration first`, without touching any file,
because its startup cleanup would otherwise take every old screenshot for an orphan.

The migration is a one-time script kept outside this repository (design: section 8 of
`docs/superpowers/specs/2026-10-25-server-ingest-design.md`): stop the service, back up
`~/.local/share/vitality/client`, run the script (it renames the files into interaction
folders, fills the new tables and drops the old one), check the counts, then start the
service again.

## Upload to a server

Optional. Each interaction is uploaded as soon as it ends, to a `vitality-server` that is
only reachable over Tailscale (set it up with [`server/README.md`](../server/README.md)).
Without `upload_server` in `client.toml`, nothing is uploaded and `status` shows
`upload: disabled`.

1. Join the laptop to the same tailnet as the server, under the name the server's
   `allowed_clients` lists (`laptop`):

   ```sh
   sudo pacman -S tailscale
   sudo systemctl enable --now tailscaled
   sudo tailscale up
   sudo tailscale set --hostname=laptop
   ```

2. Add the server's address to `~/.config/vitality/client.toml` and restart the daemon:

   ```toml
   upload_server = "http://vitality-librarian:8100"
   ```

   ```sh
   systemctl --user restart vitality-clientd
   ```

   The name `vitality-librarian` is resolved by Tailscale's MagicDNS; if your setup does not
   resolve it, use the address from `tailscale ip -4 vitality-librarian` instead.

3. Check it: `vitality-client doctor` should pass the `tailscale` and `upload server`
   checks, and `vitality-client status` should show `upload: ok`.

Uploads retry on their own: offline or a server error backs off from 5 s to 5 min, and a
refusal because the laptop is not in `allowed_clients` is retried every 5 min. An
interaction the server rejects as invalid or too large (or whose files are missing) is skipped and
logged, not retried. Uploads continue while capture is paused. Captures are always kept
locally after they upload.

## Commands

`vitality-clientd` is the daemon itself (what the service runs). Everything else goes
through `vitality-client`, which talks to the running daemon:

| Command | Does |
|---|---|
| `vitality-client pause [DURATION]` | Pause, optionally for e.g. `30s`, `30m`, `2h` |
| `vitality-client resume` | Resume |
| `vitality-client toggle` | Pause if capturing, resume if paused (for a bar button or keybind) |
| `vitality-client status` | State, captures today, last capture, disk use, upload state, warnings, paths |
| `vitality-client status --json` | The same as one line of JSON |
| `vitality-client doctor` | Check the setup (with `upload_server` set, also Tailscale and the server) |
| `vitality-client capture` | Take a screenshot now (even while paused) and print its path. It joins the open interaction, or is its own one-shot interaction when idle |
| `vitality-client reset [--all] [--yes]` | Delete all captures and the index (asks first; the daemon must be stopped). `--all` also deletes state, so the screen-sharing picker appears again |
| `vitality-client upgrade` | Reinstall the client from this repo's `main` (runs `client/install.sh`), then `doctor` |

Pausing takes one final capture if an interaction is in progress, so pause before sensitive
content appears, not after.

`status` includes the upload state:

```
capturing · 42 captures today · last 17:00:41 · 38.2 MB
activity: available
capture: available
upload: ok · 0 pending · last 17:00:43
```

The upload state is `disabled` (no `upload_server`), `ok`, `offline` (the server cannot be
reached; interactions queue up) or `rejected` (the server refused this laptop: check
`allowed_clients`). `pending` counts finished interactions not yet uploaded; `last` is the
time of the last successful upload. With no server set, the line is just `upload: disabled`.
`status --json` carries it as an `upload` object (`state`, `pending`, `last_upload`).

### Pause keybinding (Hyprland)

```
bind = SUPER, F12, exec, vitality-client pause
bind = SUPER SHIFT, F12, exec, vitality-client resume
```

### Status bar (Waybar)

`status --json` prints one line with `state` (`idle`, `active` or `paused`),
`captures_today` (shots taken today), `disk_warning`, `upload` and more, and exits 1 with no output on stdout when the
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

`client/.venv` is only for tests and type checking, not for running the daemon:

```bash
uv venv --python /usr/bin/python3 --system-site-packages   # once, in client/
uv sync
uv run pytest     # tests
uv run pyright    # type check: implementations must match their interfaces
```

Editing the repo never changes the running daemon; run `vitality-client upgrade` (or
`./client/install.sh`) to install a new version.

Layout: `daemon/` is `vitality-clientd` and the parts only it uses (policy, activity,
frames, storage, upload); `cli/` is `vitality-client`; `shared/` holds what both use: `config.py`
(paths and settings), `state.py` (saved pause state) and `channel.py` (the socket
protocol). Neither `daemon/` nor `cli/` imports the other. The folder layout and the upload
messages are shared with the server through the `protocol/` package at the repo root, a path
dependency of this project.

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
settle_seconds = 2     # quiet time after the last input that ends an interaction (last shot)
max_gap_seconds = 10   # longest gap between shots while activity continues
webp_quality = 85
disk_warning_gb = 20   # status and the log warn above this; nothing is deleted
# upload_server = "http://vitality-librarian:8100"   # unset = no uploads (see above)
```

Files follow the XDG convention; `status` and `doctor` print the exact paths.

| What | Default location |
|---|---|
| Config | `~/.config/vitality/client.toml` |
| Captures and `index.sqlite` | `~/.local/share/vitality/client/` (layout below) |
| Pause state, portal restore token | `~/.local/state/vitality/client/state.json` |
| Control socket | `$XDG_RUNTIME_DIR/vitality/client.sock` |

### Stored captures

Each burst of activity is one **interaction**, a folder named by its UTC start time:

```
~/.local/share/vitality/client/
  index.sqlite                     tables: interactions, shots
  captures/2026-10-25/17-00-00/
    0000.webp                      the screen just before the first input
    0004.webp                      later shots: whole seconds since the start
    0014.webp
    interaction.json               start/end, local time and zone, the list of shots
```

`interaction.json` appears when the interaction ends, so a folder without one is still in
progress (an interrupted one is closed the next time the daemon starts). The same layout is
what the server stores, and it is what gets uploaded. A manual `capture` while idle is an
interaction of its own with a single shot.

## Security note

Membership in the `input` group lets every process of your user read all input devices,
which weakens Wayland's protection against keylogging. This is accepted for v0 on a
single-user machine you own. The input watcher reduces each event to a timestamp before it
leaves its module, so it can later be split into a separate process running as a dedicated
system user; that split is required before this runs on anyone else's machine. Logs never
contain key data, pointer data or image contents.

With `upload_server` set, your screenshots leave the laptop, over Tailscale, to the server
named there (see its [README](../server/README.md) for how they are stored). They are not
encrypted at rest on the server.
