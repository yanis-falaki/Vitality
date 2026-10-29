# Vitality — Docker Compose for the VM

**Date:** 2026-10-29
**Status:** Draft for review

## 1. Purpose

Run the VM side of Vitality (the server and the observer) with Docker Compose instead of
systemd units and install scripts. One `compose.yaml` defines everything that runs on the VM:
what each service is, what it can read and write, and where the data lives. Installing becomes
clone + `docker compose up -d`; upgrading becomes one command for both services.

What must not change: uploads stay Tailscale-only and identify devices by their Tailscale name;
captures stay read-only to the observer; the observer uses Claude Code with a subscription login; the existing
captures and observations carry over; the laptop client needs no change.

## 2. Scope

**In:**
- `compose.yaml`, `.env.example`, a Dockerfile per service, the `./vitality` helper script.
- Settings from environment variables (from `.env`) instead of TOML files.
- One command per package with subcommands (`vitality-server serve|status|healthcheck`,
  `vitality-observer run|status`), replacing `vitality-serverd`, `vitality-observerd` and the
  old `vitality-server status|doctor|upgrade`.
- An observer status line.
- Removing the install scripts, systemd units, TOML settings and the systemd-era commands.
- Migrating the live VM, with the owner, step by step.

**Out (later):**
- Published images (GitHub building and pushing images; the VM pulling instead of building).
- Experiment tooling (a prompt setting, a run-once mode).
- `doctor` for either service.
- Any change to the laptop client beyond wording.

## 3. Decisions

| Question | Decision | Why |
|---|---|---|
| Images | Built on the VM from the checkout (`build:` in compose) | No registry or CI needed; trying a branch is `docker compose run --rm --build …` |
| Whose files | Both containers run as the owner (uid/gid from `.env`, default 1000) | Every file is the owner's: no groups, no sudo; containers still isolate the services |
| Data | Bind mounts under one data folder, `~/vitality-data` by default | Plain files, easy to reach and back up; outside the git checkout |
| Claude login | The owner's own login on the VM (`~/.claude/`, whose `.credentials.json` holds it), mounted into the observer | One login, switched with the normal `/login`; it needs a machine where `claude` writes that file (a server, not a desktop with a keyring); the observer uses whatever account the owner's `claude` uses, and the owner's Claude settings (hooks, permissions, MCP servers) apply to its runs too |
| Settings | One `.env` next to `compose.yaml`; compose passes values as environment variables | One place for every setting, the usual Compose way |
| Admin commands | Run in a throwaway container from the service's image (`docker compose run --rm`) | The installed code lives in the image; works even when the service is stopped |
| `doctor` | Replaced by a Compose healthcheck on the server, plus start-up errors in the logs | Its systemd checks no longer apply; the server already checks its settings at start |
| `upgrade` | `git pull` + `docker compose up -d --build`, via `./vitality upgrade` | One upgrade for everything; nothing installed outside the images |
| Command names | `vitality-server serve`, `vitality-observer run` (no `…d` names) | Compose service names are what the owner sees; one entry point per package |

## 4. Layout

**Repository** (new or changed):
```
compose.yaml                 the two services
.env.example                 every setting with its default (committed)
.env                         the owner's copy (git-ignored)
vitality                     the helper script
server/Dockerfile
observer/Dockerfile
```

**On the VM:**
```
~/Vitality/                  the checkout: code, compose.yaml, .env
~/vitality-data/             all Vitality data, owned by the owner
  server/                    captures/, tmp/, index.sqlite
  model/                     observations/
  observer/                  calls.jsonl, skipped.txt
~/.claude/                   the owner's own Claude Code login and settings, shared with the observer
```

**Inside the containers** (fixed by the mounts; also the code's defaults):

| Container path | Host source | server | observer |
|---|---|---|---|
| `/data` | `$VITALITY_DATA/server` | read-write | — |
| `/captures` | `$VITALITY_DATA/server/captures` | — | read-only |
| `/model` | `$VITALITY_DATA/model` | — | read-write |
| `/state` | `$VITALITY_DATA/observer` | — | read-write |
| `/home/observer` | a private tmpfs (HOME) | — | read-write |
| `/home/observer/.claude` | the owner's `~/.claude/` | — | read-write |
| `/var/run/tailscale` | the host's directory of that name | yes | — |
| `/etc/localtime` | the host's | read-only | read-only |

The server gets Tailscale's socket directory, not the socket file: a restarted `tailscaled` makes a
new socket file, which a single-file mount would keep pointing at the old, dead one.

## 5. Settings

`.env.example`, with defaults:
```
# Vitality settings. Copy to .env (git-ignored) and edit: cp .env.example .env
# Only SERVER_ALLOWED_CLIENTS must be filled in; everything else has the default shown.
# (Comments sit on their own lines: after an empty value, a trailing "# ..." would become the value.)

# Where the data lives, and who owns it. compose.yaml supplies these defaults, so leave them
# commented out unless yours differ.
#   VITALITY_DATA: the data folder; unset: $HOME/vitality-data
#   VITALITY_UID / VITALITY_GID: who owns the data; the containers run as this user and group
# VITALITY_DATA=/home/you/vitality-data
# VITALITY_UID=1000
# VITALITY_GID=1000

# Served on the VM's Tailscale address only.
SERVER_PORT=8100
# Comma-separated Tailscale device names that may upload, e.g. laptop
SERVER_ALLOWED_CLIENTS=

OBSERVER_BATCH_SIZE=10
OBSERVER_CONTEXT_SIZE=15
OBSERVER_CALL_TIMEOUT_SECONDS=600
# Empty: Claude Code's default model.
OBSERVER_MODEL=
```

Every comment is on its own line, never after a value: Compose reads a trailing `# ...` after an
empty value (`SERVER_ALLOWED_CLIENTS=   # ...`) as the value itself.

`compose.yaml` supplies the defaults for the first three (`${VITALITY_DATA:-${HOME}/vitality-data}`,
`${VITALITY_UID:-1000}`, `${VITALITY_GID:-1000}`), so a fresh `.env` needs only
`SERVER_ALLOWED_CLIENTS`.

Each package's settings module becomes "read these variables, fall back to these defaults",
with the same validation as today (positive numbers, finite timeout, a clear message naming the
bad variable). Paths are settings too (`SERVER_DATA_DIR`, `OBSERVER_CAPTURES_DIR`,
`OBSERVER_MODEL_DIR`, `OBSERVER_STATE_DIR`), defaulting to the container paths above; compose
does not set them, but they let the programs run outside a container (tests, the laptop).

## 6. Images

**Server** (`server/Dockerfile`): Python 3.14; the protocol and server packages and their
dependencies from `server/uv.lock` (locked versions, as `install.sh` did); the `tailscale`
command-line program, pinned. Runs `vitality-server serve` with the host's network
(`network_mode: host`), so it binds the VM's Tailscale address and `SERVER_PORT` as today.
The `tailscale` program talks to the VM's own Tailscale through the mounted socket.

**Observer** (`observer/Dockerfile`): Python 3.14; the observer package from `observer/uv.lock`;
Claude Code at a pinned version, installed in the image (not in the home), with self-updating
off; `bash` and the basic tools Claude's Bash tool uses, plus one for cropping or zooming images.
Its home (`HOME=/home/observer`) is a private tmpfs, so Claude Code can write its caches; the
owner's `~/.claude/` is mounted inside it, which is where the login comes from
(`.credentials.json`). `~/.claude.json` is not shared: Claude Code saves it by renaming a new file
over the old one, which a single-file bind mount breaks in both directions, so the container keeps
its own in the tmpfs home. Runs `vitality-observer run`.

**Both**, in `compose.yaml`:
- `user: ${VITALITY_UID}:${VITALITY_GID}`, `restart: unless-stopped`.
- `read_only: true` with a private `/tmp` (tmpfs), `security_opt: no-new-privileges`,
  `cap_drop: ALL`: the systemd sandbox, as container settings.
- Log rotation: the json-file driver with a size cap (10 MB × 3 files per service).
- The server's healthcheck: `vitality-server healthcheck` (GET `/health` on its Tailscale
  address and port; exit 0 when it answers) every 30 s.

## 7. Commands

**`vitality-server`:**
- `serve`: the server (what `vitality-serverd` did).
- `status`: one line per device (interactions, shots, size, last received in local time) and
  the free space on the data disk. Today's `status` without the paths block.
- `healthcheck`: as above; for Docker, not for people.

**`vitality-observer`:**
- `run`: the observer (what `vitality-observerd` did; refuses root as before).
- `status`: one line:
  ```
  observer: 70 / 1103 observed · 0 skipped · last call 21:28:26 ok (40 s)
  ```
  Interactions counted as the observer counts them; skipped from `skipped.txt`; the last call
  from the last line of `calls.jsonl` (local time; `ok`, or the failure detail; duration).
  `no calls yet` when there is no log.

**`./vitality`** (bash, in the repo root; prints each `docker compose …` line before running it):
```
./vitality setup             create .env from .env.example if missing, and the data folders
                             (server/captures, model, observer) owned by the owner; check that
                             the owner's claude is logged in (~/.claude/.credentials.json exists)
./vitality status            docker compose ps; then the server's and the observer's status
                             (each via `docker compose run --rm`); then the data folder from .env
./vitality logs [service]    docker compose logs -f
./vitality upgrade           git pull (fast-forward only; lists the new commits), then
                             docker compose up -d --build
```

Docker creates a missing bind-mount source owned by root, which the containers, running as the
owner, could not use; `setup` creates the data folders first and refuses to go on until the owner's
Claude login file exists. On a desktop with a keyring the login is not in that file, so the VM side
runs on a server. A fresh install, with Claude Code installed and logged in as the owner (`claude`, then
`/login`), is:
```
git clone https://github.com/yanis-falaki/Vitality.git ~/Vitality && cd ~/Vitality
./vitality setup                 # then set SERVER_ALLOWED_CLIENTS in .env
docker compose up -d
```

## 8. What is removed

- `server/install.sh`, `observer/install.sh`, `server/systemd/`, `observer/systemd/`.
- `server/server.toml.example`, `observer/observer.toml.example`, and the TOML loaders.
- The server's `doctor`, `upgrade`, their checks and helpers, and their tests.
- The `vitality-serverd` and `vitality-observerd` entry points.
- READMEs and specs: the server and observer READMEs are rewritten for Compose; earlier specs
  stay as history.

## 9. Migration (with the owner, one step at a time)

1. Stop and disable `vitality-serverd` and `vitality-observerd`. The laptop client keeps its
   interactions and retries.
2. Copy the data into `~/vitality-data` (`server/`, `model/`, `observer/`) and make the owner
   own it. Copy, not move: the old setup stays intact until the new one is proven.
3. `git pull`; `./vitality setup`; set `SERVER_ALLOWED_CLIENTS=laptop` in `.env`;
   `docker compose build`.
4. Log the owner's own `claude` on the VM into the account the observer should use (`claude`, `/login`).
5. `docker compose up -d`; `./vitality status`: server healthy, a laptop upload arrives, the
   observer writes a batch.
6. When the owner is satisfied: remove the old units, `/opt/vitality-{server,observer,python}`,
   the `/usr/local/bin` links, `/etc/vitality`, the `vitality` and `vitality-librarian` accounts
   and their home, and the old `/srv` copies.

Until step 6, going back is `docker compose down` and re-enabling the two old units.

## 10. Testing

- Unit tests: settings from environment (defaults, overrides, invalid values) for both
  packages; the subcommands; the observer status line; the server status without paths.
- `docker compose config` with `.env.example` resolves cleanly.
- Build both images locally; inside them, `claude --version`, `tailscale version`, and
  `vitality-observer status` against a sample data folder; start the server image against the
  laptop's own Tailscale (socket mount, healthcheck turning healthy).
- The shared login, on the VM before `docker compose up -d`: in the observer image, with the
  owner's `~/.claude/` mounted, `claude -p "say ok" --output-format json` works without the
  owner's `~/.claude.json`, and a token refresh is written back to `~/.claude/.credentials.json`.
  If it does not work, the fallback is a long-lived token: `claude setup-token`, then
  `CLAUDE_CODE_OAUTH_TOKEN` in `.env`. On a desktop with a keyring the login is in the keyring,
  not in that file, so this setup needs a machine where `claude` writes `.credentials.json`.
- `bash -n vitality` (and shellcheck if available).
- End to end: migration step 5, on the VM.

## 11. Done when

- `docker compose up -d` on the VM runs both services; `./vitality status` shows the server
  healthy, the laptop's uploads, and the observer's progress.
- New laptop interactions arrive and get observed; captures are read-only to the observer.
- `./vitality upgrade` takes both services to the latest `main`.
- Nothing Vitality-related is left in `/opt`, `/etc/vitality`, `/srv` or the system accounts.
