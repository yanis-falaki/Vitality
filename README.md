# Vitality

Vitality watches how you use your computer and builds a model of you from it. A client on
your laptop captures screenshots around your keyboard and mouse activity; a server on a VM
stores them, and an observer sends each interaction's screenshots to Claude through the Anthropic API
and writes down what it saw: a verbatim transcript and a list of the operations the person did.

## The two parts

| Part | Where | What it is | How it runs |
|---|---|---|---|
| [`client/`](client/README.md) | the laptop | captures interactions and uploads them | its own `install.sh` and a systemd user service |
| `server/`, `observer/` | the VM | [`server/`](server/README.md) stores uploads; [`observer/`](observer/README.md) turns them into observations and operations | Docker Compose, from this checkout |

`protocol/` is the small package the client and server share. The laptop is set up from
[`client/README.md`](client/README.md); everything below is about the VM.

## Install on the VM

You need:

- Docker with the Compose plugin, usable without `sudo`: your user in the `docker` group
  (`sudo usermod -aG docker $USER`, then log in again). Under `sudo`, `$HOME` is root's, so
  `docker compose` would mount the wrong data folder.
- Tailscale joined to your tailnet (the client README addresses the VM as `vitality-librarian`:
  `sudo tailscale set --hostname=vitality-librarian`).
- An Anthropic API key for the observer (see [The observer's API key](#the-observers-api-key)).
- Claude Code, logged in as the user who will own the data: run `claude`, then `/login` inside it.
  The observer does not use this login; the modeler of a later phase will, and `./vitality setup`
  already checks for it. Claude Code keeps it in `~/.claude/.credentials.json` on a server; on a
  desktop with a keyring it is kept in the keyring instead, which a container cannot use, so run
  the VM side on a server.

```sh
git clone https://github.com/yanis-falaki/Vitality.git ~/Vitality && cd ~/Vitality
./vitality setup        # creates .env, then stops until ANTHROPIC_API_KEY is set in it
# edit .env: ANTHROPIC_API_KEY and SERVER_ALLOWED_CLIENTS
./vitality setup        # again
docker compose up -d
```

`./vitality setup` creates `.env` from `.env.example` (readable only by you: it holds the API
key) and the data folders (also readable only by you: they hold screenshots). It makes
`model/` a git repo with two starting files, and it stops if the Claude login file is missing
or `ANTHROPIC_API_KEY` is empty in `.env`. `docker compose up` refuses to start a service whose
data folder is missing, rather than letting Docker create it owned by root. In `.env`, put the
Tailscale device names that may upload, for example `SERVER_ALLOWED_CLIENTS=laptop`. An empty
list refuses every upload. The first `docker compose up -d` builds both images, which takes a
few minutes. Then check:

```sh
./vitality status
```

## The `./vitality` commands

Each one prints the `docker compose` or `git` commands it runs.

| Command | What it does |
|---|---|
| `./vitality setup` | creates `.env`, the data folders and the `model/` git repo if missing; checks the Claude login and the API key. Safe to rerun |
| `./vitality status` | `docker compose ps`, then the server's and the observer's status, then the data folder |
| `./vitality logs [service]` | follows the logs; `service` is `server` or `observer` (default: both) |
| `./vitality upgrade` | fast-forwards to the newest `main` (only from a clean `main`), then `docker compose up -d --build` |
| `./vitality lab <args>` | runs `vitality-lab` (a stage on a fixed dataset, outside `vitality-data`) with the key and settings from `.env`; see [`lab/README.md`](lab/README.md) |

`status` looks like this:

```
laptop: 595 interactions · 1835 shots · 201.0 MB · last received 21:30:37
disk: 19.7 GB free
observer: 560 / 595 observed · 2 skipped · last call 21:28:26 EDT ok (40 s)
```

To stop everything: `docker compose down`. The data stays.

## The observer's API key

The observer calls the Anthropic API with a key of its own, not your Claude Code login. Make a
workspace API key in the [Claude Console](https://platform.claude.com) (your own organization;
add credit under Billing) and put it in `.env` on its own line:

```
ANTHROPIC_API_KEY=sk-ant-...
```

Without a key the observer refuses to start (the log says so), while the server and every other
`docker compose` command carry on. The key is in `.env` only (git-ignored, mode 600) and in the
observer container's environment; it is never written to the data folder or logged.

Cost depends on how much you use the computer. At about 150 interactions a day on the default
model (`claude-sonnet-5-5`, called directly) it is roughly $110 a month; observing the existing
backlog of 1,103 interactions once costs about $25. `OBSERVER_MODE=batch` sends the same
requests through the Batches API at half price, and observations then arrive minutes to an hour
later instead of seconds. `calls.jsonl` records the cost of every call (see
[`observer/README.md`](observer/README.md)). If the credit runs out, the observer pauses for 6
hours and tries again; nothing is lost or skipped.

## Where the data lives

Everything is plain files under one folder, `~/vitality-data` by default, owned by you:

```
~/vitality-data/
  server/      captures/<device>/<day>/<time>/ (the interactions), tmp/, index.sqlite
  model/       a git repo (started by ./vitality setup, commit "model: start")
    observations/<device>/<day>/<time>.md   transcript of each interaction (the observer)
    operations/<device>/<day>/<time>.md     the operations in it, each with a confidence (the observer)
    actions/, activities/, strivings/       built from the operations by the modeler (a later phase)
    user-context.md                         about you, written by you; the modeler reads it, never edits it
    modeler-notes.md                        the modeler's notes to its next run
  observer/    calls.jsonl, skipped.txt
  modeler/     the modeler's run logs (a later phase)
~/vitality-archive/observations-v1/   the old observations, moved out of the data folder by the migration
```

The observer reads `server/captures` read-only and writes only `model/observations` and
`model/operations`; the rest of `model/` is not mounted into it.
`user-context.md` is yours to fill in when you like (roles, routines, priorities, stresses,
the people who matter); the model works without it. The formats are in
[`usermodel/README.md`](usermodel/README.md).

Nothing here is encrypted, and the VM is not backed up: the laptop keeps every capture, so
for now the laptop is the backup.

### `~/.claude`

The observer no longer mounts `~/.claude`, so a screenshot containing text meant to steer it (a
prompt injection) cannot reach your Claude Code settings through it: the observer has no tools
and no files but its own two output folders. The modeler of phase 2 will use your Claude Code
login and will mount `~/.claude`; the risks of that are discussed when it arrives.

## Changing settings

Settings live in `.env`, next to `compose.yaml`; [`.env.example`](.env.example) lists every
one with its default. After editing:

```sh
docker compose up -d
```

Compose recreates only the services whose settings changed. Rules for `.env`:

- Put each value on its own line and never add a trailing comment: after an empty value
  (`SERVER_ALLOWED_CLIENTS=   # ...`), Compose reads the comment as the value. Comments go on
  their own lines.
- `VITALITY_DATA`, if you set it, is a full path such as `/home/you/vitality-data`, not `~`
  or `$HOME`.
- A bad value stops the service at start, and the log names the variable
  (`SERVER_PORT must be an integer from 1 to 65535, got 'abc'`).
- `ANTHROPIC_API_KEY` is a secret: `.env` is mode 600 and git-ignored; do not paste it elsewhere.

## Logs

```sh
./vitality logs              # both services, followed
./vitality logs observer     # one of them
docker compose ps            # state and health; the server has a healthcheck
```

Logs are kept by Docker (3 files of 10 MB per service) and never contain image contents. If
the server will not start, the log says why; the usual cause is that Tailscale is not up on
the VM, since the server refuses to listen anywhere else. What each service logs is in its
README.

## Upgrading

```sh
./vitality upgrade
```

Nothing is installed outside the images, so an upgrade is the new code plus a rebuild of both.
It refuses to run off `main` or with modified tracked files, and says why.

## Development

Each package has its own tests and type check, run from its folder:

```sh
uv run pytest -q
uv run pyright
```

The designs are in [`docs/superpowers/specs/`](docs/superpowers/specs/); the Compose setup is
[`2026-10-29-compose-design.md`](docs/superpowers/specs/2026-10-29-compose-design.md).
