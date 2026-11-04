# Vitality modeler

The modeler turns the observer's evidence (operations and observations) into the user model: actions, activities and strivings, written as Markdown files in a git repository. The writing is done by a Claude Code agent. This package is the harness around it: it stages a batch of evidence, runs one session, checks the result with `vitality-usermodel check`, and commits it or reverts it. The design is in `docs/superpowers/specs/2026-11-04-modeler-design.md`.

Milestone 1 is the lab: `vitality-modeler replay <dataset> <run>` replays a dataset batch by batch, as the scheduler later will on live evidence.

## The image

Build from the repository root:

    docker build -f modeler/Dockerfile -t vitality-modeler .

The image holds Python 3.14, the `vitality-modeler` and `vitality-usermodel` commands, git, and Claude Code at a pinned version (`CLAUDE_CODE_VERSION`, installed with the native installer, auto-update off). The container is the isolation: the agent sees only what is mounted, not the owner's files, settings, memory or plugins.

It runs as any `--user uid:gid` (`/home/modeler` and `/home/modeler/.claude` are mode 1777; git trusts every folder). The lab runs it without Compose:

    docker run --rm --user "$(id -u):$(id -g)" \
      -v <dataset>:/dataset:ro \
      -v <run>:/run \
      -v ~/.claude/.credentials.json:/home/modeler/.claude/.credentials.json \
      -e MODELER_MODEL=... -e MODELER_EFFORT=... \
      vitality-modeler vitality-modeler replay /dataset /run

The credentials file is the only thing of the owner's that is mounted. Claude Code may refresh the token in it.

A dataset is a folder with `operations/`, `observations/` (both `<device>/<date>/<time>.md`) and `user-context.md` (who the owner is; it seeds the model). `<run>` must be a folder without a `model/` in it.

## What a session does

`replay` creates `<run>/model` (a git repository with `user-context.md` and `modeler-notes.md`), then repeats until all operations are committed:

1. Copy the oldest whole operations files (with their observations), up to `MODELER_MAX_OPERATIONS` operations, into the model, untracked.
2. Run Claude Code in the model folder, with the playbook appended to its system prompt and only project settings. Its tools are the files, `vitality-usermodel check` and `next-id`, and git.
3. Check the model. If there are problems, resume the same session once with them.
4. Commit when the check passes and every offered operation is covered; otherwise revert the session's changes. The evidence stays in the model and is offered to the next session.

The replay stops when everything is committed, after 3 sessions in a row without a commit, when the usage limit is reached, or on Ctrl-C or SIGTERM.

## Settings

Environment variables; unset or empty keeps the default.

| Variable | Default | Meaning |
| --- | --- | --- |
| `MODELER_MODEL` | `claude-opus-5-5` | Model of the session |
| `MODELER_EFFORT` | `high` | `low`, `medium`, `high`, `xhigh` or `max` |
| `MODELER_MAX_OPERATIONS` | `500` | Most operations offered to one session (whole files, so the first file may exceed it) |
| `MODELER_RUN_TIMEOUT_SECONDS` | `3600` | A session lasting longer is given up on |

## Records

In `<run>/`:

- `model/`: the user model, one commit per successful session.
- `sessions.jsonl`: one JSON line per session: outcome (`committed`, `reverted`, `failed`, `limited`), operations offered, model, playbook version, attempts, check errors, commit, changes, duration, tokens, cost.
- `transcripts/<run>.jsonl`: the session's full stream.
- `replay.json`: sessions per outcome, why the replay stopped (`null` if all was committed), and the interactions never committed.

Exit code: 0 when all was committed, 1 when the replay stopped early or failed, 2 on a usage error.

## The playbook, and how to iterate on it

The playbook is the agent's instructions: `modeler/vitality_modeler/playbook.md`. Its version (first 12 hex digits of its SHA-256) is recorded with each session, so any edit is visible in `sessions.jsonl`.

To iterate: run a replay, read the model and the transcripts, edit `playbook.md`, rebuild the image, and replay into a new run folder. Compare runs by their model folders and by `replay.json` and the check errors in `sessions.jsonl`. A run folder is never reused.

## Isolation probe

This checks that a Claude Code session in the container sees none of the owner's own setup or files. It runs a real session, so the owner runs it on the VM (not the agent). It mounts only the credentials file and an empty folder as `/run`, and starts in an empty model folder.

    mkdir -p /tmp/probe-run/model
    docker run --rm -v ~/.claude/.credentials.json:/home/modeler/.claude/.credentials.json \
      -v /tmp/probe-run:/run -v "$PWD/modeler/probe.md":/probe.md:ro \
      -w /run/model --user "$(id -u):$(id -g)" vitality-modeler \
      sh -c 'claude -p "$(cat /probe.md)" --setting-sources project --strict-mcp-config --permission-mode bypassPermissions'

(Run from the repository root; `probe.md` is the run message.)

What to look for in the answer:

- No memory or CLAUDE.md files, no plugins, no MCP servers. Skills and tools only the built-in ones.
- Working directory `/run/model`, empty.
- `/home/modeler/.claude` holds `.credentials.json` and nothing of the owner's (a session may create its own cache files there; that is fine).
- `/etc/hostname` is the container's, and nothing under `/` looks like the owner's files.
- The usage-limit text, if it knows it (the harness recognizes a limited session by it; see `claude.py`).

Result: not yet run

Afterwards, check that the owner's own Claude Code on the VM still works (`claude -p hi`): the mounted credentials file may have been refreshed by the container's session.
