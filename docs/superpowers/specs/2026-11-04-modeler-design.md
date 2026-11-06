# Vitality — Modeler design (phase 2, milestone 1)

**Date:** 2026-11-04
**Status:** Draft for review

## 1. Purpose

Build the modeler of the user-model spec (`2026-11-03-user-model-design.md`, §8) far enough to try it
in the lab: one Claude Code session turns a batch of evidence (observations and operations) into
actions, activities and strivings in a model folder, checked and committed. The owner runs it on a
day or two of real evidence, reads what it built, adjusts the playbook and runs again. The
scheduler and the modeler service come after, once the results are good.

This spec amends the user-model spec where they differ (§3); everything else there still holds
(levels, file formats, IDs, links, the check of §9, `runs.jsonl` of §11).

## 2. Scope

**In (milestone 1):**
- The session harness: prepare, run Claude Code isolated, check, resume once, commit or revert,
  record.
- The first playbook: an orchestrator with three staged sub-agents.
- The modeler's Docker image, which every session runs in.
- Evidence datasets in the lab, and `vitality-lab run modeler <dataset>`, which replays the
  scheduler over a dataset batch by batch, in one container per lab run.

**Later (milestone 2, own plan):** the scheduler (triggers, lock, pausing after three failed runs,
waiting out the seat's usage limits), the modeler as a service on the VM, `./vitality status` and
`logs modeler`, working through the real backlog.

**Out:** anything that reads the model for the owner (an ambient agent, feedback, a UI).

## 3. Changes to the user-model spec

| There | Here | Why |
|---|---|---|
| At most 50 operations offered to one run (`MODELER_MAX_OPERATIONS=50`) | At most **500** (about a day) | 50 operations is about 20 interactions, half an hour: too narrow to see intent shift, and each session's fixed cost is paid too often. In steady running the triggers (20 waiting, or 5 idle minutes) still start sessions on small batches; the cap matters for backlogs and lab replays. |
| The playbook as one agent's ordered steps | An orchestrator delegating to three staged sub-agents (§6) | Tempo's staged pipeline; each stage gets a fresh context, so large batches fit. |
| `MODELER_MODEL` empty (Claude Code's default) | `claude-opus-5-5` | The owner's choice for the modeler overall. |
| — | `MODELER_EFFORT` (default `high`) | Effort is a deliberate setting, as for the observer. |
| `MODELER_RUN_TIMEOUT_SECONDS=1800` | `3600` | Batches of up to 500 operations with sub-agents. |
| Claude Code started with defaults | Started isolated from the user's Claude Code configuration (§5.2) | A session's behaviour must depend only on the playbook, the run message and the model folder. |
| The containerized modeler with the scheduler | The image in milestone 1; lab runs use it too (§7) | A session runs with no permission checks, so it must only reach the files it is given; and the lab must run what production will run. |

## 4. Evidence datasets

Made by hand, from `~/vitality-data/model` once the backlog is observed, for a stretch the owner
remembers:

```
~/vitality-lab/datasets/<name>/
  README.md                          which stretch, why, when copied
  observations/<device>/<day>/<time>.md
  operations/<device>/<day>/<time>.md
  user-context.md                    the owner's, frozen with the dataset
```

A dataset holds either `captures/` (for the observer) or `observations/` and `operations/` (for the
modeler); `vitality-lab run <stage>` refuses a dataset of the wrong kind. The first modeler dataset
waits for the owner to fill in `user-context.md`.

## 5. The session harness

Shared by the lab now and the scheduler later. Input: a model folder (a git repo), the evidence to
offer, settings. Output: a committed or reverted session and its record.

### 5.1 Prepare

1. Refuse if the model folder has changes other than untracked evidence files (the owner's
   uncommitted edits are never committed or reverted by a session).
2. Choose the batch: the oldest operations files not yet in any commit, up to `MODELER_MAX_OPERATIONS`
   operations, whole files only, oldest first by local start time. Stage them and their observations.
   Record the start commit.

### 5.2 Run Claude Code once

```
claude -p <run message> --append-system-prompt <playbook.md>
  --model <MODELER_MODEL> --effort <MODELER_EFFORT>
  --setting-sources project --strict-mcp-config
  --permission-mode bypassPermissions --output-format stream-json --verbose
  --forward-subagent-text --disallowedTools EnterWorktree ExitWorktree
  [--add-dir <captures folder>]
```

`--forward-subagent-text` puts the sub-agents' text into the stream, so the transcript holds it.
The worktree tools are disallowed and the playbook forbids worktree isolation: work outside the model
folder would never be checked or committed. A revert removes nested repositories and prunes worktrees.

Working directory: the model folder. Timeout: `MODELER_RUN_TIMEOUT_SECONDS`. Sub-agents allowed.
Authentication is the owner's Claude Code login on the VM (the Team seat), never an API key:
`ANTHROPIC_API_KEY` is never passed into the container, and the harness also removes it from the
session's environment, so the seat is what is used.

**Isolation** comes from the container (§7, §8): it holds the installed code, Claude Code and git,
and sees only what is mounted, which is the model folder (or a lab run's folder), the captures
read-only when given, and the login file (`~/.claude/.credentials.json`, read-write so a refreshed
token is saved). No user `CLAUDE.md`, settings, plugins, MCP servers, `.env`, repo or other data are
inside. **Isolation probe** (once, by hand, before the first lab run, and after Claude Code upgrades):
a session in the container is asked to report its loaded memory files, plugins, skills and MCP
servers and to list what it can read outside its working folder; it must report none of the user's.
The probe also confirms the login works in the container and that the owner's Claude Code on the VM
still works afterwards (the two share the login file).

**The run message** (built by the harness):
- the offered operations files, each with its local start time;
- every activity and striving, one line each: id, title, status, `part_of`;
- that `user-context.md` and `modeler-notes.md` exist and must be read first;
- where the captures are, if mounted (screenshots, read-only).

### 5.3 Check, resume, commit or revert

1. `vitality-usermodel check --since <start commit>`.
2. Failing: resume the same session once (`claude -p --resume <session id>`) with the errors; check
   again.
3. **Pass:** stage everything, commit as `modeler run <id>: <counts>`.
   **Fail, timeout, crash:** reset to the start commit, keep the offered evidence untracked, remove
   files the session created. Outcome `reverted` or `failed`.
4. **Seat limit reached** (Claude Code reports the usage limit): revert as above; outcome `limited`.
   It is not a failure of the session.

### 5.4 Record

One line per session (`sessions.jsonl` in the lab, `runs.jsonl` in production), the fields of the
user-model spec §11 plus `"outcome": "limited"`. `changes` are counted by the harness from the git
diff. Tokens and cost are what the stream reports (for a seat, cost is informational). The session's
full stream (sub-agents included) is kept as `transcripts/<id>.jsonl`.

## 6. The playbook (`modeler/vitality_modeler/playbook.md`)

The main agent is an orchestrator. It:

1. Reads `modeler-notes.md` and `user-context.md`. User context is background, never evidence.
   Times of day come only from local times inside files; file and folder names are UTC identifiers.
2. Delegates **actions** to one sub-agent: the offered operations into actions at shifts of
   intent, covering every operation exactly once.
3. Delegates **activities** to a sub-agent: file each new action under existing activities or
   propose new ones (`tentative`), then reconcile the affected activities: match, revise, merge or
   create. Promote to `established` at 3+ actions over 2+ sessions with confidence 6+.
4. Delegates **strivings** to a sub-agent, only if activities changed meaningfully (for instance, a
   clear, recurring pattern of distinct activities that may serve a striving): synthesize, then
   critique each against the three tests (recurs with no endpoint; shows in behaviour; abstracts over
   several activities); split, merge, retitle or drop; keep conflicting strivings both, linked by
   `conflicts_with`.
5. Reviews: changes only with evidence, reasons in file bodies, no locked file or evidence touched;
   updates `modeler-notes.md` (open questions, hunches, things to watch); runs
   `vitality-usermodel check` and fixes what it reports.

**The agent's commands.** The session has two commands from phase 1 on its `PATH` (installed in the
image, from the same commit as the rest of the code): `vitality-usermodel check`, which reports every
file whose frontmatter does not parse or misses a field, every bad ID or broken link, and every
offered operation in no action or in two (the rules of the user-model spec §9), and
`vitality-usermodel next-id <kind>`, which hands out the next activity or striving number. The
playbook tells the agent and each sub-agent to run `check` after writing files and to take IDs only
from `next-id`. The harness runs the same check afterwards (§5.3), so a session cannot pass by not
running it.

The playbook is text in the repo, versioned by its hash in every record; iterating on it is what the
lab is for.

## 7. `vitality-lab run modeler <dataset> [--label …] [--set …]`

As the lab spec's observer stage (refusals, run folder name, `changes.diff`, `run.yaml`), but the
run itself happens in **one container**:

- **On the host** (`vitality-lab`): check git and the dataset, create the run folder, write
  `run.yaml` and `changes.diff`, build the modeler image from the current code (cached when nothing
  changed: `docker build -f modeler/Dockerfile -t vitality-modeler .`), prepare the run's model
  (step 1), then start one container
  with plain Docker (no Compose: Compose describes the running application, and the lab is apart
  from it):
  `docker run --rm --user <VITALITY_UID>:<VITALITY_GID> -v <run>/model:/model -v <run>/state:/state
  -v ~/.claude/.credentials.json:<the login path in the container> vitality-modeler
  vitality-modeler replay /model /state`: the model and the records at the paths the service will
  use (`/model`, `/state`). The dataset, `run.yaml`, `playbook.md` and `changes.diff` are never mounted. Every mount and setting the container gets is in that one
  command, which `vitality-lab` prints before running it. When it exits, fill in
  `run.yaml`'s result from `sessions.jsonl` and the replay's summary.
- **In the container** (`vitality-modeler replay`), steps 2–3 below, then exit.

1. **Setup (host):** the run folder gets `model/`, a fresh git repo with the templates and the
   dataset's `user-context.md`, committed, plus the dataset's whole `observations/` and `operations/`,
   untracked, as the service's model folder holds a backlog; `state/` is created empty; `playbook.md`
   is copied beside `run.yaml`.
2. **Replay (container):** run one session (§5) after another; each is offered the oldest evidence not
   yet committed, up to `max_operations` operations, and may read all the rest. Repeat until all of
   the dataset's evidence is committed. (So a session can see evidence later than its batch, as when
   the service works through a backlog; live arrival is not simulated.)
3. **Stop early** after 3 sessions in a row without a commit, on `limited`, or on Ctrl-C; `run.yaml`
   records the reason and which evidence was not offered.
4. **Captures (optional, `--captures`):** the host copies the dataset's interactions' capture folders
   from the data folder into the run's `captures/`, mounted read-only at `/captures` and given to each
   session with `--add-dir /captures` and in the run message; `run.yaml` records `captures`.
5. **Settings:** `MODELER_MODEL`, `MODELER_EFFORT`, `MODELER_MAX_OPERATIONS`,
   `MODELER_RUN_TIMEOUT_SECONDS` from `.env`; `--set` short names `model`, `effort`,
   `max_operations`, `run_timeout_seconds`.
6. **Records:** `run.yaml` (stage `modeler`, input `datasets/<name>`, playbook hash, sessions by
   outcome, stop reason), and in `state/`: `sessions.jsonl`, `transcripts/`, `replay.json`.

Reading a run: its `model/` folder, and `git -C model log --stat` for how it grew session by session.

## 8. Code layout

```
modeler/
  pyproject.toml           depends on vitality-usermodel; command vitality-modeler
  Dockerfile               as the observer's: code installed into /opt/venv (not editable, no repo
                           in the final image), plus Claude Code (a pinned version) and git
  vitality_modeler/
    session.py             §5: prepare, run, check/resume, commit/revert, record (Contract first)
    claude.py              building the claude command and run message; reading the stream
    repo.py                clean test, staging, commit, revert, change counts (git)
    replay.py              §7: prepare (host), then session after session, stopping early, summary
    main.py                vitality-modeler replay <model> <state>  (milestone 2 adds `run`, `status`)
    playbook.md            shipped inside the package, read with importlib.resources
  tests/                   with a fake `claude` executable
lab/vitality_lab/
  modeler_run.py           §7 host side: checks, run folder, image build, the container, result
  cli.py                   gains `run modeler`; dataset kind check
```

`compose.yaml` is unchanged in milestone 1; milestone 2 adds the `modeler` service, built from the
same `modeler/Dockerfile`. `./vitality lab` also passes the `MODELER_*` settings.

## 9. Testing

With a fake `claude` (a script in the tests that edits the model folder as told, writes a
stream-json transcript, and can exit with errors, time out or report the usage limit); no real
sessions:

- prepare: refuses a dirty folder; stages exactly the offered evidence; whole files, oldest first,
  up to the cap;
- check passes → one commit with counts; fails then passes on resume; fails twice → reverted, files
  the session created removed, evidence back to untracked;
- timeout and crash → failed and reverted; usage limit → `limited`;
- the record line and transcript; `ANTHROPIC_API_KEY` absent from the session's environment;
- the lab's host side with a fake container command: the run folder, `run.yaml` before and after,
  the arguments and mounts it would start the container with, no `ANTHROPIC_API_KEY` among them;
- the image builds, and `vitality-modeler replay` runs in it against the fake `claude` (a smoke
  test run by hand on the VM, not in the unit tests);
- the lab replay: several batches → several sessions and commits; stops after 3 sessions without a
  commit, on `limited`, on Ctrl-C; a captures dataset refused for the modeler stage and vice versa.

End to end: the isolation probe, then the first lab run on the owner's chosen days, read together.

## 10. Done when

- The isolation probe passes, and its outcome is recorded in the modeler README.
- `./vitality lab run modeler <dataset>` on a day of real evidence leaves a model that passes the
  check, one commit per session, and complete records.
- The owner has read the result and the playbook has had at least one round of changes in the lab.
