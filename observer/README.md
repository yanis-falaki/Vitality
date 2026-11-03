# vitality-observer

Turns every interaction that `vitality-server` has stored into **evidence** for the user model:
an **observation** (a word-for-word Markdown transcript of what was on screen) and an
**operations** file (the distinct things the person did, each with a confidence from 1 to 10).
Both are written from the interaction's screenshots by Claude, through the Anthropic API. The
design is in [`docs/superpowers/specs/2026-11-03-user-model-design.md`](../docs/superpowers/specs/2026-11-03-user-model-design.md)
(section 7); the file formats are in [`usermodel/README.md`](../usermodel/README.md).

It runs as the `observer` service of Docker Compose, started by `vitality-observer run`, on the
same VM as the server. To install, run, configure, upgrade or read its logs, see the
[root README](../README.md); this page is about what the observer does. It needs an
`ANTHROPIC_API_KEY` (see the root README) and refuses to start without one, or as root. It has
no Claude Code, no login and no tools: it sends a request and writes what comes back.

## What is where

Inside the container, with the host folder each comes from (`~/vitality-data` by default):

```
/captures/                                  ~/vitality-data/server/captures (read-only; the server's interactions)
/model/observations/<device>/<day>/<time>.md   ~/vitality-data/model/observations   the transcript of each interaction
/model/operations/<device>/<day>/<time>.md     ~/vitality-data/model/operations     its operations
/state/                                     ~/vitality-data/observer
  calls.jsonl                                 one line per interaction attempted
  skipped.txt                                 interactions given up on
```

Only the two `model/` subfolders are mounted, not the rest of the model (a git repo that the
modeler of a later phase works in). `<device>/<day>/<time>` is the interaction's name, for
example `laptop/2026-10-25/17-00-00`: a file's path is its interaction's name under
`observations/` or `operations/`. An interaction counts as observed when both files exist and
are not empty.

## What one observation is

For each interaction the observer makes one request to the Messages API: a header with the
interaction's name and its start and end in the device's own time zone, then every screenshot
in order, each labelled with its offset in seconds, with the instructions in
`vitality_observer/prompt.md` as the system prompt. It gives Claude no earlier observations and
no `user-context.md`, so what is written stays independent of what the model believes.

The reply is constrained to a JSON schema, `{transcript, operations: [{text, confidence}]}`, and
checked again (non-blank text, at least one operation, confidence 1 to 10). The code, not
Claude, assigns the operation IDs (`op1`, `op2`, ...), adds the frontmatter (interaction,
device, local start and end) and writes the two files, observation first and operations last.

## Settings

Set in `.env` (see the [root README](../README.md#changing-settings)):

| Variable | Default | Meaning |
|---|---|---|
| `ANTHROPIC_API_KEY` | none | the API key; required |
| `OBSERVER_MODE` | `direct` | `direct`: one request per interaction, answered at once. `batch`: the same requests through the Batches API at half price |
| `OBSERVER_PARALLEL` | 4 | most interactions observed at the same time (a round in direct mode) |
| `OBSERVER_MODEL` | `claude-sonnet-5-5` | the model; empty keeps the default |
| `OBSERVER_EFFORT` | `low` | thinking effort: `low`, `medium`, `high`, `xhigh` or `max` |
| `OBSERVER_CALL_TIMEOUT_SECONDS` | 600 | a request running longer is given up on |

A bad value stops the observer at start, and the log names the variable. The folders
(`OBSERVER_CAPTURES_DIR`, `OBSERVER_MODEL_DIR`, `OBSERVER_STATE_DIR`) default to the container
paths above; Compose does not set them, but they let the program run outside a container.

## Commands

| Command | What it is |
|---|---|
| `vitality-observer run` | the observer itself; Compose runs it |
| `vitality-observer status` | how far it has got, in one line |

`./vitality status` runs `status` in a throwaway container. It only reads.

```
observer: 560 / 595 observed · 2 skipped · last call 21:28:26 EDT ok (8 s)
```

Observed out of all interactions in the captures, the entries in `skipped.txt`, and the last
line of `calls.jsonl` (local time, then `ok` or `failed: <detail>`, then its duration), or
`no calls yet`.

## How it behaves

It finds new work by watching the captures folder and by rescanning it every 60 seconds. A
round takes the oldest unobserved interactions across all devices:

- **Direct mode:** up to `OBSERVER_PARALLEL` interactions, one request each, run at the same
  time. Each observation is written seconds after its interaction arrives. When a round
  finishes the observer looks again, so a backlog is worked through oldest first.
  Direct requests use the API's server-side fallback: if the model refuses a request, the API
  may answer with another model instead of failing it. `calls.jsonl` records the model that
  really answered.
- **Batch mode:** up to 100 interactions go out as one Message Batch, and the observer polls
  until it ends (results come in minutes to an hour, at most 24 hours). Batches do not fall
  back to another model. A batch that has not ended 25 hours after it was submitted is given up
  on. Restarting the observer while a batch is in flight submits that batch again, so it is paid
  for twice.

Failures follow three rules.

1. **Back off.** A round that wrote nothing is a failure: the observer sleeps 5 seconds, then
   doubles the pause each time up to 5 minutes; a round that wrote something, or having nothing
   to do, starts it over. When the API is rate limiting and gives a `retry-after`, the observer waits that long (at least 5 seconds) instead.
2. **Pause when out of credit.** A billing error or "credit balance is too low" pauses the
   observer for 6 hours, logs one warning, and then tries again. Nothing is skipped: the account
   is the problem, not the interaction. Top up in the Console and restart the service
   (`docker compose restart observer`) to resume sooner.
3. **Strike.** An interaction gets a strike when a request failed by its own fault: the model
   refused it (`refused (<category>)`), its reply was cut off or did not match the schema, the
   API rejected the request (status 400 or 413), or its `interaction.json` or a screenshot is
   missing or invalid. Three strikes and it is **skipped**: added to `skipped.txt` with the last
   reason and never offered again. Nothing else counts: not a network failure, a timeout, a rate
   limit, an out-of-credit error, a failure of a whole batch, or files that could not be
   written. Nor a round of two or more where every request was rejected with the same 400 or 413
   error: that points at the request, not the interactions, so the round just backs off and logs
   one warning naming the error.

A skip is cheap to undo: the captures are kept, and deleting the interaction's line from
`skipped.txt` offers it again (see below). Strikes are kept in memory only, so a restart clears
them. A failure is logged when it starts and again whenever its reason changes.

The watcher on the captures folder (inotify) stops if the system's watch limit is hit; the
observer logs a warning and carries on, and the 60-second rescan keeps things working.

## Logs and cost

`./vitality logs observer` shows what it did: rounds observed, failures, waits, skips.

Each interaction attempted adds one line to `calls.jsonl`
(`~/vitality-data/observer/calls.jsonl` on the VM), for example:

```json
{"time": "2026-11-03T14:23:10Z", "interaction": "laptop/2026-11-03/14-22-05", "ok": true,
 "detail": "", "model": "claude-sonnet-5-5", "prompt": "5c2e8a91b7d0", "batch": null,
 "screenshots": 3, "operations": 2, "duration_ms": 8400, "input_tokens": 5120,
 "output_tokens": 1430, "cache_read_input_tokens": 1200, "cost": 0.0125}
```

| Field | Meaning |
|---|---|
| `time` | when the attempt started, UTC |
| `interaction` | its name |
| `ok`, `detail` | whether both files were written, and the reason if not |
| `model` | the model that answered (null if no reply came) |
| `prompt` | the first 12 hex digits of the SHA-256 of `prompt.md`, to tell prompt versions apart |
| `batch` | the batch ID in batch mode; null for direct calls |
| `screenshots`, `operations` | screenshots sent; operations written |
| `duration_ms` | how long it took (for a batch, the whole batch) |
| `input_tokens`, `output_tokens`, `cache_read_input_tokens` | usage reported by the API |
| `cost` | dollars, computed from the published per-million-token prices in `provider/anthropic_api.py` (halved in batch mode); null when the model has no listed price or there was no reply |

Inputs are not stored: they are the capture folder and the prompt. For example, calls and cost
per day (needs `jq`):

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

- **An observation is wrong and should be redone.** Delete its two files (or either one): the
  interaction counts as unobserved again, and the new files replace them:

  ```sh
  rm ~/vitality-data/model/observations/laptop/2026-10-25/17-00-00.md \
     ~/vitality-data/model/operations/laptop/2026-10-25/17-00-00.md
  ```

  An empty file counts as missing. To redo everything, delete the contents of both folders: the
  backlog is then worked through again, and costs what it cost the first time (about $25 for
  1,103 interactions in direct mode).

## Development

```sh
uv run pytest
uv run pyright
```

The tests never call the real API; they use a fake client in its place. The model provider is
behind the `Observer` Protocol in `vitality_observer/provider/interface.py` (the implementation
for the Anthropic API is `provider/anthropic_api.py`), and the loop behind `Loop` in
`vitality_observer/loop.py`.
