# vitality-observer

Turns every interaction that `vitality-server` has stored into an **observation**: a short
Markdown account of what the person did, written by Claude from the interaction's
screenshots. Observations are the first layer of the user model; the design is in
[`docs/superpowers/specs/2026-10-27-observer-design.md`](../docs/superpowers/specs/2026-10-27-observer-design.md).

It runs as the `observer` service of Docker Compose, started by `vitality-observer run`, on the
same VM as the server. To install, run, configure, upgrade or read its logs, see the
[root README](../README.md); this page is about what the observer does.

It finds new work by watching the captures folder and by rescanning it every 60 seconds. For
each batch of unobserved interactions it runs Claude Code headless (`claude -p`) in the model
folder, and Claude writes the observation files. The observer can read the captures but not
change them: Compose mounts them read-only. It uses the owner's own Claude Code login
(the `~/.claude/` folder), mounted into the container, and refuses to run as root.

## What is where

Inside the container, with the host folder each comes from (`~/vitality-data` by default):

```
/captures/                                 ~/vitality-data/server/captures (read-only; the server's interactions)
/model/                                    ~/vitality-data/model (the user model)
  observations/<device>/<day>/<time>.md      one observation per interaction
/state/                                    ~/vitality-data/observer
  calls.jsonl                                one line per Claude call
  skipped.txt                                interactions given up on
```

`<device>/<day>/<time>` is the interaction's name, for example `laptop/2026-10-25/17-00-00`:
an observation's path is its interaction's name under `observations/`.

## Settings

Set in `.env` (see the [root README](../README.md#changing-settings)):

| Variable | Default | Meaning |
|---|---|---|
| `OBSERVER_BATCH_SIZE` | 10 | most interactions handed to one Claude call |
| `OBSERVER_CONTEXT_SIZE` | 15 | earlier observations of the same device given as context |
| `OBSERVER_CALL_TIMEOUT_SECONDS` | 600 | a Claude call running longer is killed |
| `OBSERVER_MODEL` | empty | model for Claude Code; empty is its default |

The folders (`OBSERVER_CAPTURES_DIR`, `OBSERVER_MODEL_DIR`, `OBSERVER_STATE_DIR`) default to the
container paths above; Compose does not set them, but they let the program run outside a
container.

## Commands

| Command | What it is |
|---|---|
| `vitality-observer run` | the observer itself; Compose runs it |
| `vitality-observer status` | how far it has got, in one line |

`./vitality status` runs `status` in a throwaway container. It only reads.

```
observer: 560 / 595 observed · 2 skipped · last call 21:28:26 EDT ok (40 s)
```

Observed out of all interactions in the captures, the entries in `skipped.txt`, and the last
line of `calls.jsonl` (local time, then `ok` or `failed: <detail>`, then its duration), or
`no calls yet`.

## How it behaves

The observer makes one Claude call at a time. It takes the oldest unobserved interaction and
the next ones from the same device (other devices' wait their turn), up to the batch size,
strictly oldest first within the device, and gives Claude the last
context-size observations of that device before them. When a call finishes it looks again,
so a backlog is worked through in batches, oldest first, and when it is idle a new interaction
is picked up at once.

A call that writes at least one of its observations has worked, even if Claude reported an
error. A call that writes none has failed (an error, a hang cut off at the call timeout, or
simply nothing written). Failures follow three rules:

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

`./vitality logs observer` shows what it did: batches observed, failures, waits, skips.

Each Claude call also adds one line to `calls.jsonl` (`~/vitality-data/observer/calls.jsonl` on
the VM): `time`, the `interactions` in it, `exit` code, `ok`, `detail` (why it failed),
`duration_ms`, `cost` (Claude Code's estimate in USD), `input_tokens`, `output_tokens`,
`cache_read_input_tokens`, `cache_creation_input_tokens` and the `models` it used (Claude Code
sometimes adds a small one for side tasks). For example, calls and cost per day (needs `jq`):

```sh
jq -s 'group_by(.time[:10]) | map({day: .[0].time[:10], calls: length, cost: (map(.cost // 0) | add)})' \
  ~/vitality-data/observer/calls.jsonl
```

## Putting right what it got wrong

The data folder is yours, so these are plain file edits on the VM; the observer notices at its
next look (at most a minute).

- **An interaction was skipped.** Each line of `skipped.txt` is an interaction name, a tab and
  the reason. Delete the interaction's line and it is offered again, with fresh strikes:

  ```sh
  sed -i '\|^laptop/2026-10-25/17-00-00\t|d' ~/vitality-data/observer/skipped.txt
  ```

- **An observation is wrong and should be redone.** Delete its file. The interaction counts
  as unobserved again, and the new observation replaces it:

  ```sh
  rm ~/vitality-data/model/observations/laptop/2026-10-25/17-00-00.md
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
