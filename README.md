# Vitality

Vitality watches how you use your computer and builds a model of you from it. A client on
your laptop captures screenshots around your keyboard and mouse activity; a server on a VM
stores them, and an observer has Claude write a short Markdown observation of each interaction.

## The two parts

| Part | Where | What it is | How it runs |
|---|---|---|---|
| [`client/`](client/README.md) | the laptop | captures interactions and uploads them | its own `install.sh` and a systemd user service |
| `server/`, `observer/` | the VM | [`server/`](server/README.md) stores uploads; [`observer/`](observer/README.md) turns them into observations | Docker Compose, from this checkout |

`protocol/` is the small package the client and server share. The laptop is set up from
[`client/README.md`](client/README.md); everything below is about the VM.

## Install on the VM

You need Docker with the Compose plugin, Tailscale joined to your tailnet (the client README
addresses the VM as `vitality-librarian`: `sudo tailscale set --hostname=vitality-librarian`),
and Claude Code, logged in as the user who will own the data:
run `claude`, then `/login` inside it. The observer uses that login, which Claude Code keeps in
`~/.claude/.credentials.json` on a server; on a desktop with a keyring it is kept in the keyring
instead, which the container cannot use, so run the VM side on a server.

```sh
git clone https://github.com/yanis-falaki/Vitality.git ~/Vitality && cd ~/Vitality
./vitality setup        # then set SERVER_ALLOWED_CLIENTS in .env
docker compose up -d
```

`./vitality setup` creates `.env` from `.env.example` and the data folders (readable only by
you: they hold screenshots), and stops if the Claude login file is missing. `docker compose up`
refuses to start a service whose data folder or `~/.claude` is missing, rather than letting
Docker create it owned by root. In `.env`, put the
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
| `./vitality setup` | creates `.env` and the data folders if missing; checks the Claude login. Safe to rerun |
| `./vitality status` | `docker compose ps`, then the server's and the observer's status, then the data folder |
| `./vitality logs [service]` | follows the logs; `service` is `server` or `observer` (default: both) |
| `./vitality upgrade` | fast-forwards to the newest `main` (only from a clean `main`), then `docker compose up -d --build` |

`status` looks like this:

```
laptop: 595 interactions · 1835 shots · 201.0 MB · last received 21:30:37
disk: 19.7 GB free
observer: 560 / 595 observed · 2 skipped · last call 21:28:26 EDT ok (40 s)
```

To stop everything: `docker compose down`. The data stays.

## Where the data lives

Everything is plain files under one folder, `~/vitality-data` by default, owned by you:

```
~/vitality-data/
  server/      captures/<device>/<day>/<time>/ (the interactions), tmp/, index.sqlite
  model/       observations/<device>/<day>/<time>.md (what the observer writes)
  observer/    calls.jsonl, skipped.txt
~/.claude/                   your own Claude Code login (.credentials.json) and settings, shared with the observer
```

The observer reads `server/captures` read-only. Only the `~/.claude/` folder is shared, not
`~/.claude.json` (Claude Code replaces that file by renaming, which a single-file mount breaks);
the observer keeps its own in memory. Its Claude Code runs with your login and your
`~/.claude/` settings, so the account your `claude` uses is the one that pays for it, and
your hooks, permissions and MCP servers apply to its runs too. To switch accounts, run
`claude` and `/login` as usual.

Nothing here is encrypted, and the VM is not backed up: the laptop keeps every capture, so
for now the laptop is the backup.

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
