# vitality-observer

Turns every interaction that `vitality-server` has stored into an **observation**: a short
Markdown account of what the person did, written by Claude from the interaction's
screenshots. Observations are the first layer of the user model; the design is in
[`docs/superpowers/specs/2026-10-27-observer-design.md`](../docs/superpowers/specs/2026-10-27-observer-design.md).

It runs on the same VM as the server, as a systemd service, `vitality-observerd`, under its
own account, `vitality-librarian`. It finds new work by watching the captures folder and by
rescanning it every 60 seconds. For each batch of unobserved interactions it
runs Claude Code headless (`claude -p`) in `/srv/vitality-model`, and Claude writes the
observation files. The account can read the captures but not change them: they belong to
`vitality`, and the unit mounts `/srv/vitality` read-only.

## What is where

```
/opt/vitality-observer/                   the installed observer (its prompt.md ships inside it)
/usr/local/bin/vitality-observerd         the service's command
/etc/systemd/system/vitality-observerd.service
/etc/vitality/observer.toml               settings
/var/lib/vitality-librarian/              the account's home
  .claude/                                  Claude Code's login
  .local/bin/claude                         Claude Code itself (updates itself)
  calls.jsonl                               one line per Claude call
  skipped.txt                               interactions given up on
/srv/vitality-model/                      the user model, a git repository (750, owned by vitality-librarian)
  observations/<device>/<day>/<time>.md     one observation per interaction
/srv/vitality/captures/<device>/<day>/<time>/   the interactions (written by vitality-server)
```

`<device>/<day>/<time>` is the interaction's name, for example `laptop/2026-10-25/17-00-00`:
an observation's path is its interaction's name under `observations/`.

## Setup

1. Install and start the server first (`./server/install.sh`, see [`server/README.md`](../server/README.md)).

2. On the VM, from the repo checkout:

   ```sh
   ./observer/install.sh
   ```

   The script installs Python 3.14 with `uv` and the observer, with its dependencies at the
   exact versions (and hashes) in `observer/uv.lock`, into `/opt/vitality-observer`. It
   creates the `vitality-librarian` system user (home `/var/lib/vitality-librarian`, no login
   shell, in group `vitality` so it can read the captures), adds you to the
   `vitality-librarian` group (so you can read the model; log in again once), creates
   `/srv/vitality-model` as a git repository, writes `/etc/vitality/observer.toml` (never
   overwriting an existing one), installs and enables the systemd unit, and starts it if Claude
   Code is ready (step 3). Rerunning it upgrades the code: it stops the service while the
   program is replaced and starts it again. To update, pull the repo and run it again.

3. Install Claude Code for the `vitality-librarian` account and log it in, once. The install
   script does not do this for you: Claude Code lives in the account's own home, where
   Anthropic's installer puts it and keeps it up to date, and logging in needs a browser.
   `install.sh` ends by checking both, and prints only what is still missing.

   ```sh
   sudo -u vitality-librarian -H bash -c 'cd && curl -fsSL https://claude.ai/install.sh | bash'
   sudo -u vitality-librarian -H bash -c 'cd && ~/.local/bin/claude'
   ```

   (The `cd` is because `sudo` keeps your current directory, which the account cannot enter.)

   In the second, type `/login`, follow the link to log in to your Claude subscription, then
   exit. On a first install the service is enabled but not yet started, which simply avoids
   a service that fails and retries until you log in. Once Claude Code is ready, start it:

   ```sh
   sudo systemctl start vitality-observerd
   ```

   The unit puts `/var/lib/vitality-librarian/.local/bin` first on `PATH`, which is how it
   finds this `claude`. If you reboot before this is done, the service starts anyway; that is
   harmless, because a failure that never reaches Claude never skips an interaction.

## Configuration

`/etc/vitality/observer.toml` (see [`observer.toml.example`](observer.toml.example)):

```toml
captures_dir = "/srv/vitality/captures"     # the server's interactions; the observer only reads them
model_dir = "/srv/vitality-model"           # the user model (a git repository); observations/ lives here
state_dir = "/var/lib/vitality-librarian"   # where calls.jsonl and skipped.txt are kept
batch_size = 10                             # most interactions handed to one Claude call
context_size = 15                           # earlier observations of the same device given as context
call_timeout_seconds = 600                  # a Claude call running longer is killed
# model = "…"                               # unset: Claude Code's default
```

`model_dir` and `state_dir` must stay under the paths in `ReadWritePaths` in the unit, and
`captures_dir` under `/srv/vitality`: the service can write nowhere else. Changes take effect
after `sudo systemctl restart vitality-observerd`. To use another file, run
`vitality-observerd --config PATH`. The program refuses to run as root.

## How it behaves

The observer makes one Claude call at a time. It takes the oldest unobserved interaction and
the next ones from the same device (other devices' wait their turn), up to `batch_size`,
strictly oldest first within the device, and gives Claude the last
`context_size` observations of that device before them. When a call finishes it looks again,
so a backlog is worked through in batches, oldest first, and when it is idle a new interaction
is picked up at once.

A call that writes at least one of its observations has worked, even if Claude reported an
error. A call that writes none has failed (an error, a hang cut off at `call_timeout_seconds`,
or simply nothing written). Failures follow three rules:

1. **Back off.** After a failed call the observer sleeps 5 seconds, then doubles the pause each
   time up to 5 minutes; a call that worked, or having nothing to do, starts it over. A usage
   limit instead waits until the reset time Claude reports (30 minutes if it gives none,
   never under a minute) and leaves the pause as it was.
2. **Isolate.** After a failed call (a usage limit excepted) the next call takes only the
   oldest interaction, until a call works; then full batches resume.
3. **Strike.** An interaction gets a strike only when a call for it alone failed after Claude
   worked on it (it used tokens, or timed out). Three strikes and it is **skipped**: added to
   `skipped.txt` and never offered again. Nothing else counts: not a failed batch of several,
   not a failure that never reached Claude (a missing or logged-out `claude`, a network that
   fails at once), not a usage limit, and not files missing after a call that worked.

A skip is cheap to undo: the captures are kept, and deleting the interaction's line from
`skipped.txt` offers it again (see below). Strikes are kept in memory only, so a restart
clears them. A failure is logged when it starts and again whenever its reason changes.

The watcher on the captures folder (inotify) stops if the system's watch limit is hit; the
observer logs a warning and carries on, and the 60-second rescan keeps things working.

## Logs and cost

```sh
journalctl -u vitality-observerd -f      # what it did: batches observed, failures, waits, skips
systemctl status vitality-observerd
```

Each Claude call also adds one line to `/var/lib/vitality-librarian/calls.jsonl`: `time`,
the `interactions` in it, `exit` code, `ok`, `detail` (why it failed), `duration_ms`,
`cost` (Claude Code's estimate in USD), `input_tokens`, `output_tokens`,
`cache_read_input_tokens`, `cache_creation_input_tokens` and the `models` it used (Claude Code
sometimes adds a small one for side tasks). For
example, calls and cost per day (needs `jq`; the file may need `sudo` to read):

```sh
jq -s 'group_by(.time[:10]) | map({day: .[0].time[:10], calls: length, cost: (map(.cost // 0) | add)})' \
  /var/lib/vitality-librarian/calls.jsonl
```

## Putting right what it got wrong

You can read `/srv/vitality-model` as a member of the `vitality-librarian` group, but only
that account can change it, so these use `sudo -u vitality-librarian`.

- **An interaction was skipped.** Each line of `skipped.txt` is an interaction name, a tab and
  the reason. Delete the interaction's line and it is offered again at the next look (at most
  a minute), with fresh strikes:

  ```sh
  sudo -u vitality-librarian sed -i '\|^laptop/2026-10-25/17-00-00\t|d' /var/lib/vitality-librarian/skipped.txt
  ```

- **An observation is wrong and should be redone.** Delete its file. The interaction counts
  as unobserved again and is picked up at the next look, and the new observation replaces it:

  ```sh
  sudo -u vitality-librarian rm /srv/vitality-model/observations/laptop/2026-10-25/17-00-00.md
  ```

  An empty observation file counts as missing. To redo everything, delete the `observations`
  folder's contents: the backlog is then worked through again, and costs what it cost the
  first time.

## Development

```sh
uv run pytest
uv run pyright
```

The tests never run the real `claude`; they use a fake in its place. The Claude Code call is
behind the `Observer` Protocol in `vitality_observer/claude.py`, and the loop behind `Loop`
in `vitality_observer/loop.py`.
