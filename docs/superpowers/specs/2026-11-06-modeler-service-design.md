# Vitality — Modeler service design (phase 2, milestone 2)

**Date:** 2026-11-06
**Status:** Draft for review

## 1. Purpose

Run the modeler continuously on the VM: a Compose service next to the server and the observer that
turns new evidence in `~/vitality-data/model` into actions, activities and strivings, one session
at a time, with the harness, playbook and image of milestone 1
(`2026-11-04-modeler-design.md`). It works through the backlog first, then keeps up with live
evidence.

What stays as it is: the session harness (`session.py`, `repo.py`, `claude.py`), the image, the
lab, the check, and the principle that the modeler only consolidates evidence it is given.

## 2. Scope

**In:** the scheduler; `vitality-modeler run` and `status`; the `modeler` Compose service; the
`./vitality` changes; one playbook sentence for small batches; the rollout with the owner.

**Out:** anything that reads the model for the owner (an ambient agent, a UI); new evidence
sources; changes to the session harness.

## 3. Decisions

| Question | Decision |
|---|---|
| Backlog | No special mode: untracked evidence is waiting, so the service works through the backlog in batches up to the cap, oldest first, then continues live. |
| Small batches | The playbook tells the orchestrator to do the three stages itself, in order, when the batch is under about 100 operations, and to delegate when larger. |
| Seat usage limit | A `limited` session waits a fixed hour and tries again. |
| Failures | Three sessions in a row without a commit pause the service until it is restarted. |
| Session log | `sessions.jsonl`, as in the lab (the user-model spec's `runs.jsonl` is renamed). |
| Model | Opus 5.5 on the owner's Team seat, settings as milestone 1. |

## 4. The scheduler (`vitality-modeler run`)

A loop, checking every 30 seconds (file events through bind mounts are unreliable):

1. **Trigger.** A session is due when 20 or more operations are waiting (operations in untracked
   operations files), or when anything is waiting and no new operations file has appeared for
   5 minutes (each new file restarts the 5 minutes).
2. **Session.** `run_session(model, state, config, PLAYBOOK, PLAYBOOK_VERSION, trigger=…,
   captures="/captures")`, `trigger` being `20 operations` or `idle`: the oldest waiting
   operations up to `MODELER_MAX_OPERATIONS` (500).
3. **After it.**
   - `committed`: check again at once (more may be waiting, as during catch-up).
   - `reverted` or `failed`: count it; after 3 in a row without a commit, **pause**: no more
     sessions until the service restarts; logged as an error and shown by `status`. The offered
     evidence stays waiting.
   - `limited`: not counted; wait 1 hour, then resume.
4. **The owner's edits.** If the model folder has changes other than new evidence, `run_session`
   refuses; the scheduler logs it once and waits until the folder is clean. Not a failure.
5. **One at a time.** A lock file in `/state` (held for the scheduler's life, `fcntl.flock`) makes
   a second scheduler on the same state refuse to start.
6. **Stopping.** SIGTERM (`docker compose stop`) stops the loop; a session in progress is killed and
   reverted by the harness, so the model is never left half-written.

What the scheduler is doing is kept in `/state/status.json` (`state`: `idle`, `running`,
`paused`, `waiting-limit` with `until`, `waiting-edits`; `since`), written on every change, for
`status` to read.

## 5. The service

`compose.yaml` gains `modeler`, shaped like `observer`: image `vitality-modeler` built from
`modeler/Dockerfile`, command `vitality-modeler run`, `init: true`, the common user
(`VITALITY_UID:VITALITY_GID`), environment `MODELER_MODEL`, `MODELER_EFFORT`,
`MODELER_MAX_OPERATIONS`, `MODELER_RUN_TIMEOUT_SECONDS` (defaults as milestone 1; never
`ANTHROPIC_API_KEY`). Mounts (missing sources are errors, never created by Docker):

| Container | Host | |
|---|---|---|
| `/model` | `$VITALITY_DATA/model` | read-write: the model repo |
| `/state` | `$VITALITY_DATA/modeler` | read-write: `sessions.jsonl`, `transcripts/`, `status.json`, the lock |
| `/captures` | `$VITALITY_DATA/server/captures` | read-only |
| `/home/modeler/.claude/.credentials.json` | `~/.claude/.credentials.json` | the owner's login |

The observer and the modeler share the model folder safely: the observer only adds new evidence
files (untracked, waiting); a session commits only its batch and its own files.

## 6. Commands

- `vitality-modeler status` (and `./vitality status`): one line, e.g.
  `modeler: 2412 operations waiting · last session 14:32 committed (9 actions, 2 activities) · running`;
  the last part is the scheduler's state (`paused (3 failed)`, `waiting (usage limit until 15:40)`,
  `waiting (uncommitted edits in the model folder)`).
- `./vitality logs modeler`: the progress lines (one per session, as in the lab), pauses and waits.
- `./vitality setup`: creates `$VITALITY_DATA/modeler` if missing and checks that
  `~/.claude/.credentials.json` exists.
- `./vitality upgrade`: unchanged; it rebuilds and restarts every service, the modeler included.

## 7. Code

```
modeler/vitality_modeler/
  scheduler.py     §4: the loop (Contract first; clock, sleep and the session function injected)
  status.py        §6: the status line from /model and /state
  main.py          gains `run` and `status`
  playbook.md      the small-batch sentence
compose.yaml, vitality, .env.example, README.md, modeler/README.md
```

## 8. Testing

The scheduler with a fake session function, a fake clock and sleep, and a temporary model folder:
triggers at 20 waiting and after 5 quiet minutes (reset by a new file); straight on after a commit;
pause after 3 without a commit; an hour's wait after `limited`; waiting while there are the
owner's edits; the lock refusing a second scheduler; SIGTERM stopping cleanly; `status.json` at
each step. `status` output for each state. One end-to-end test of `vitality-modeler run` with the
fake `claude`: two batches modeled, then idle.

## 9. Rollout (with the owner, on the VM)

1. Pull; `./vitality setup`; `docker compose up -d --build modeler`.
2. Watch the first catch-up session in `./vitality logs modeler`; read its commit in the model.
3. Let it work through the backlog (about 6 sessions at 500, an hour or so).
4. With the laptop client capturing again, check it keeps up.

## 10. Done when

The backlog is modeled; new evidence is modeled within about 10–30 minutes of arriving; `status`
and the logs say what the modeler is doing.
