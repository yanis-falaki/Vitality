# Vitality — Lab design

**Date:** 2026-11-04
**Status:** Draft for review

## 1. Purpose

Try a stage of the pipeline on a small, fixed set of interactions and read what it produced,
before running it on everything. The first use is the observer: read its transcripts and
operations for about 50 interactions, change the prompt, run again, compare. Later the modeler
uses the same lab, and each stage can be varied while the other is held fixed.

Judgement is qualitative for now: the owner reads the outputs and may write notes. There are no
metrics, graders or scores.

What must not change: `vitality-data` belongs to the running application and the lab never writes
to it; the observer service and its behaviour are untouched.

## 2. Scope

**In:**
- The lab folder and its layout.
- `vitality-lab run observer` and `vitality-lab list`, a small `lab/` package in the repo, reached
  through `./vitality lab …` on the VM.
- What a run records, so that it can be explained later.

**Out:**
- Commands to build datasets: they are made by hand (shell commands on the VM).
- Metrics, automated grading, a comparison UI. Comparing is reading two run folders (or `diff -r`).
- Resuming or retrying runs.
- Modeler runs: the format leaves room for them (§8); they are built in phase 2.

## 3. Decisions

| Question | Decision |
|---|---|
| Production service or separate harness? | A separate harness that drives the observer's core (`AnthropicObserver`) directly: the same prompt, request, parsing, writing and `calls.jsonl` as production, without the loop, strikes, skips, pauses or Docker. |
| Where results live | `~/vitality-lab` (`LAB_DIR` in `.env`), outside `vitality-data`. Not a git repo. |
| What a dataset is | A folder whose `captures/` has the server's layout, holding copies of chosen interactions. Never changed once made. |
| What identifies a run | What produced it, recorded automatically: commit, uncommitted diff, settings, input. The folder name is generated; a label is an optional nickname. |
| The prompt | Part of the code (`observer/vitality_observer/prompt.md`). To try a prompt, edit the file; the run records the diff and keeps a copy. |
| Config | `.env` as in production, with per-run overrides (`--set`), recorded. |
| Runs | Immutable. A new variant is a new run. A run can be the input of a later stage. |

## 4. Layout

```
~/vitality-lab/
  datasets/<name>/
    README.md                     how it was picked, when, from what
    captures/<device>/<YYYY-MM-DD>/<HH-MM-SS>[-n]/   interaction.json and screenshots
  runs/<YYYY-MM-DDTHH-MM-SS>-<commit>[-dirty][-<label>]/
    run.yaml                      §6
    changes.diff                  uncommitted changes (only when dirty)
    prompt.md                     the observer prompt the run used
    observations/  operations/    as in the model folder
    calls.jsonl                   as in production
    notes.md                      the owner's reading notes (optional, written by hand)
```

Run folder names use local time. Datasets are copied from `~/vitality-data/server/captures`.

## 5. Commands

Run on the VM from `~/Vitality`. `./vitality lab <args>` loads `ANTHROPIC_API_KEY`, the observer
settings and `LAB_DIR` from `.env`, then runs `uv run --project lab vitality-lab <args>`.

### `vitality-lab run observer <dataset> [--label <label>] [--set <name>=<value> …]`

1. **Check the code.** It refuses, listing them, if any untracked files exist under `observer/`,
   `usermodel/`, `protocol/` or `lab/` (they would be missing from `changes.diff`). It refuses if the
   dataset is missing or holds no interactions, or if the API key is empty.
2. **Settings.** The observer settings from `.env` (`OBSERVER_MODEL`, `OBSERVER_EFFORT`,
   `OBSERVER_PARALLEL`, `OBSERVER_CALL_TIMEOUT_SECONDS`), then `--set` overrides by short name
   (`model`, `effort`, `parallel`, `call_timeout_seconds`), validated as in production. Mode is
   always direct.
3. **Create the run folder** and write `prompt.md`, `changes.diff` (if dirty) and `run.yaml`
   without a result.
4. **Observe** every interaction in the dataset, oldest first, `parallel` at a time, through
   `AnthropicObserver` with `captures_dir` = the dataset's `captures/`, `model_dir` and `state_dir`
   = the run folder. Progress is printed per interaction.
   - A failure of the interaction's own (`reached_model`: a bad reply, a refusal, a bad
     interaction) is recorded and the run goes on.
   - Otherwise the account, not the interaction, was the problem (rate limit, overload, a server or
     connection error, a timeout): the run waits what the outcome asks (`wait_seconds`), or 30 s
     when it names no wait, if that is at most 5 minutes, and tries that interaction again; after
     5 waits it counts as failed.
   - A longer wait stops the run (running out of credits asks for 6 hours): that interaction and
     the remaining ones are recorded as not run, with the reason.
   - Ctrl-C stops it the same way.
5. **Finish.** Fill in `run.yaml`'s end time and result, and print the counts, the failures, the
   total cost and the run folder.

### `vitality-lab list`

One line per dataset (name, interaction count) and per run (folder name, stage, input, ok/failed,
cost), oldest first.

## 6. `run.yaml`

```yaml
stage: observer
input: datasets/spread-50          # a dataset; later, for the modeler, runs/<observer run>
started: 2026-11-04T15:30:02-05:00
finished: 2026-11-04T15:34:40-05:00
commit: ab8ae6a1f…                 # full hash of HEAD
dirty: true                        # changes.diff holds the difference
label: richer-prompt               # or null
settings: {model: claude-sonnet-5-5, effort: low, parallel: 4, call_timeout_seconds: 600}
prompt_version: 3f9a0c1d2e4b
result: {ok: 49, failed: 1, not_run: 0, cost_usd: 1.07}
failures:
  laptop/2026-10-27/14-03-11: "reply did not match the schema"
```

`finished` and `result` are null while the run is going or if it crashed. The cost is the sum of
`cost_usd` in the run's `calls.jsonl`.

## 7. Datasets for now

Made by hand on the VM, each with a `README.md` listing how it was picked and the names it holds:

- `spread-50`: 50 interactions evenly spaced in time across all captured ones, all devices.
- `seq-50` (optional): 50 consecutive interactions of one device, from a stretch the owner
  remembers.

## 8. The modeler later

`vitality-lab run modeler runs/<observer run>` will copy that run's observations and operations
into a fresh model folder, run the modeler once, and record the same `run.yaml` with
`stage: modeler` and the observer run as `input`. Holding one stage's run fixed while varying the
other is how each is ablated. The modeler is not deterministic, so a variant may need a few runs.

## 9. Code layout

```
lab/
  pyproject.toml          depends on vitality-observer (path dependency)
  vitality_lab/
    cli.py                argument parsing; run, list
    record.py             run folder name, run.yaml, git state (commit, dirty, diff, untracked)
    observer_run.py       the observer stage: settings, driving AnthropicObserver, results
  tests/
```

`./vitality` gains the `lab` subcommand; `.env.example` gains `LAB_DIR`; the READMEs gain a short
section.

## 10. Testing

Unit tests with a fake `Observer` and a temporary git repo; no real API calls:

- the run folder's name (clean, dirty, labelled) and contents (`prompt.md`, `changes.diff`,
  `run.yaml` before and after);
- refusal on untracked files, a missing or empty dataset, an empty API key;
- settings from the environment and `--set`, and rejection of a bad override;
- failures recorded and the run continuing; a waiting outcome retried; credits running out
  stopping the run with the rest recorded as not run;
- `list` output.

The first real run (the baseline on `spread-50`, about $1) is the end-to-end check, done with the
owner.

## 11. Done when

- `./vitality lab run observer spread-50` on the VM produces a run folder with all its files and a
  correct `run.yaml`, and touches nothing in `vitality-data`.
- A second run after editing `prompt.md` lands in its own folder, marked dirty, with the diff.
- `./vitality lab list` shows both.
