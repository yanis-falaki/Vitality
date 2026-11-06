# Modeler Service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The modeler runs as a Compose service (`vitality-modeler run`) that schedules sessions on `~/vitality-data/model`, with `vitality-modeler status` and the `./vitality` integration.

**Architecture:** `scheduler.py` is a loop around milestone 1's `run_session`, with the clock, sleep and session function injected; it keeps `/state/status.json` current and holds a lock. `status.py` reads that file, the waiting evidence and `sessions.jsonl`. Compose runs the existing image with `vitality-modeler run`.

**Tech Stack:** Python 3.12+, pytest, pyright (standard), git, Docker Compose, bash.

**Spec:** `docs/superpowers/specs/2026-11-06-modeler-service-design.md` (builds on `2026-11-04-modeler-design.md`).

## Global Constraints

- Triggers: 20 or more operations waiting, or anything waiting and no new operations file for 5 minutes (a new file restarts it); checked every 30 seconds.
- After 3 sessions in a row without a commit: pause until restart. `limited`: wait 1 hour, not counted. The owner's uncommitted edits: wait, logged once, not counted.
- Session calls: `run_session(model, state, config, PLAYBOOK, PLAYBOOK_VERSION, trigger="20 operations" | "idle", captures="/captures")`.
- `/state/status.json`: `{"state": "idle"|"running"|"paused"|"waiting-limit"|"waiting-edits", "since": ISO, "until": ISO|null}`; lock file `/state/scheduler.lock` held with `fcntl.flock` for the scheduler's life.
- Compose: mounts `/model`, `/state` (`$VITALITY_DATA/modeler`), `/captures` read-only, the credentials file; never `ANTHROPIC_API_KEY`; missing sources are errors.
- Style as the existing modeler modules; pyright clean; tests never run a real Claude session.

## Review Focus

1. Evidence arriving while a session runs: the next check sees it waiting, and it is not committed by the running session (test in Task 1).
2. A crash of the session function (an exception other than KeyboardInterrupt) does not kill the service: it counts as a failure and is logged (test in Task 1).
3. The scheduler starting with a stale `status.json` from a previous container: it overwrites it at once (test in Task 1).
4. `status` with no `sessions.jsonl` yet, and with an unreadable `status.json`: still one line, no traceback (test in Task 2).
5. Usage-limit wait across a restart: a restarted service starts fresh (no persisted wait), which is acceptable; documented (Task 3 README).

---

### Task 1: `scheduler.py`

**Files:** Create `modeler/vitality_modeler/scheduler.py`; Modify `modeler/vitality_modeler/repo.py` (one function); Test `modeler/tests/test_scheduler.py`.

**Interfaces:**
- Consumes: `run_session`, `SessionRecord` (`session.py`); `repo.foreign_changes`; `PLAYBOOK`, `PLAYBOOK_VERSION`.
- Produces:
  - `repo.waiting(model: Path) -> dict[str, int]` — untracked operations files (repo-relative path → operations in it).
  - `CHECK_SECONDS = 30`, `TRIGGER_OPERATIONS = 20`, `IDLE_SECONDS = 300`, `LIMIT_WAIT_SECONDS = 3600`, `MAX_FAILED_IN_A_ROW = 3`.
  - `class Scheduler` with `__init__(self, model: Path, state: Path, config: ModelerConfig, *, captures: str | None = "/captures", session: Callable[..., SessionRecord | None] = run_session, clock: Callable[[], float] = time.monotonic, now: Callable[[], datetime] = <aware local now>, sleep: Callable[[float], None] = time.sleep, log: Callable[[str], None] = <stderr line>)` and `run(self) -> int` (returns 1 when paused, never returns otherwise; KeyboardInterrupt ends it with 0 after writing `idle`) and `step(self) -> float` (one check; returns seconds to sleep before the next). `run` takes the lock first and raises `RuntimeError("another modeler is running")` if held.

- [ ] **Step 1: Tests** (fake session records outcomes and the trigger it got; fake clock advanced by the fake sleep; real temporary model repo via milestone 1's `init_model` and `add_evidence`):
  - `test_waits_below_trigger` (10 ops waiting, clock < 300 s → no session, status `idle`); `test_triggers_at_20` (`trigger == "20 operations"`); `test_idle_trigger_after_5_minutes` and `test_new_file_restarts_idle_timer`; `test_continues_after_commit` (two batches back to back, no sleep between); `test_pauses_after_3_failures` (status `paused`, `run` returns 1, a 4th session never called); `test_limited_waits_an_hour` (status `waiting-limit` with `until`, no session before 3600 s, then resumes; the failure count untouched); `test_waits_while_owner_edits` (edited `user-context.md` → no session, `waiting-edits`, logged once; after a commit of the edit → resumes); `test_session_exception_counts_as_failure` (Review Focus 2); `test_lock_refuses_second` ; `test_stale_status_overwritten` (Review Focus 3); `test_evidence_during_session_waits` (fake session adds an evidence file mid-call → next step sees it waiting; Review Focus 1); `test_keyboard_interrupt_writes_idle`.
- [ ] **Step 2:** Run → fail. **Step 3:** Implement. **Step 4:** Pass; pyright clean.
- [ ] **Step 5:** Commit `modeler: the scheduler`.

### Task 2: `status.py`, `main.py` (`run`, `status`), playbook sentence

**Files:** Create `modeler/vitality_modeler/status.py`; Modify `modeler/vitality_modeler/main.py`, `modeler/vitality_modeler/playbook.md`; Test `modeler/tests/test_status.py`, `modeler/tests/test_main_run.py`.

**Interfaces:**
- Produces:
  - `status_line(model: Path, state: Path, now: datetime) -> str` — `modeler: <n> operations waiting · last session <HH:MM> <outcome> (<a> actions, <b> activities) · <state>`; `no sessions yet` when there is no `sessions.jsonl`; state words `running`, `idle`, `paused (3 failed)`, `waiting (usage limit until HH:MM)`, `waiting (uncommitted edits in the model folder)`, `not running` when `status.json` is missing or unreadable.
  - `main`: `vitality-modeler run [--model /model] [--state /state] [--captures /captures]` (defaults the container paths; SIGTERM → KeyboardInterrupt; returns the scheduler's code) and `vitality-modeler status [--model …] [--state …]` (prints `status_line`, exit 0; exit 1 with `vitality-modeler: cannot read <path>` if the model folder is missing).
- Playbook: in the orchestrator's steps, one sentence: when the batch is small, under about 100 operations, do the three stages yourself, in order, following the same briefs; delegate when it is larger.

- [ ] **Step 1: Tests:** `status_line` for each state, no sessions, unreadable `status.json` (Review Focus 4), times shown in local time; `main(["status", …])`; end-to-end `test_run_models_backlog_then_idles` (real `Scheduler` with the fake `claude` on PATH, two batches, then a `KeyboardInterrupt` from the injected sleep once nothing waits → two commits, `status.json` `idle`); the playbook mentions "under about 100 operations".
- [ ] **Step 2:** Run → fail. **Step 3:** Implement. **Step 4:** Pass; pyright clean.
- [ ] **Step 5:** Commit `modeler: run and status commands; small batches without sub-agents`.

### Task 3: Compose service, `./vitality`, docs

**Files:** Modify `compose.yaml`, `vitality` (`cmd_setup` creates `$data/modeler` and checks the credentials file; `cmd_status` adds `docker compose run --rm --no-deps modeler vitality-modeler status`; usage/logs text mention `modeler`), `.env.example` (the four `MODELER_*` lines now also apply to the service), `README.md`, `modeler/README.md` (service section: what it does, status words, pausing and how to resume — `docker compose restart modeler`, the hour's wait not surviving a restart).

`compose.yaml` `modeler` service exactly as spec §5, modelled on `observer` (`<<: *common`, `init: true`, `/etc/localtime` read-only, `create_host_path: false` on every bind).

- [ ] **Step 1:** `docker compose config` validates on the hub with a temporary `VITALITY_DATA` containing the four source folders and a dummy credentials file via `HOME`; `bash -n vitality`.
- [ ] **Step 2:** Smoke on the hub: `docker compose run --rm --no-deps modeler vitality-modeler status` against the temporary data → prints a line.
- [ ] **Step 3:** Commit `modeler: the Compose service and ./vitality`.
