# vitality-lab

Runs a stage of Vitality on a small, fixed set of interactions and keeps a record of exactly what
produced the result, so outputs can be read and compared before a change goes to the real data.
The stages are the observer and the modeler. The design is in
[`docs/superpowers/specs/2026-11-04-lab-design.md`](../docs/superpowers/specs/2026-11-04-lab-design.md).

It drives the observer's own code (`AnthropicObserver`: the same prompt, request, parsing, files
and `calls.jsonl` as production) without the service around it: no loop, strikes, skips or Docker.
It never writes to `vitality-data`.

## Where things are

`$LAB_DIR`, by default `~/vitality-lab` (set it in `.env`):

```
datasets/<name>/
  README.md                       how it was picked
  captures/<device>/<day>/<time>/ copies of interactions, in the server's layout
runs/<YYYY-MM-DDTHH-MM-SS>-<commit>[-dirty][-<label>]/
  run.yaml                        what produced it and how it went
  changes.diff                    uncommitted changes the run included (when dirty)
  prompt.md                       the observer prompt it used (observer runs)
  playbook.md                     the modeler playbook it used (modeler runs)
  observations/  operations/      what the observer wrote
  calls.jsonl                     one line per API call, as in production
  notes.md                        your notes, if you write any
```

A run folder is never changed after the run. A different prompt, model or code is a new run.

## Commands

On the VM, from `~/Vitality` (it uses the API key in `.env`):

```sh
./vitality lab run observer spread-50                        # the current code and .env settings
./vitality lab run observer spread-50 --label richer-prompt  # a nickname on the folder name
./vitality lab run observer spread-50 --set effort=medium    # override for this run: model, effort,
                                                             #   parallel, call_timeout_seconds
./vitality lab list                                          # datasets and runs, oldest first
```

A run refuses to start if untracked files exist under `observer/`, `usermodel/`, `protocol/` or
`lab/` (they would run without being recorded). Uncommitted edits to tracked files are fine: the
run is marked dirty and keeps the diff. So to try a prompt, edit
`observer/vitality_observer/prompt.md` and run.

Each interaction is tried once, 4 at a time by default. A failure of the interaction's own (a bad
reply, a refusal) is recorded and the run goes on. When the API is the problem (rate limit,
overload, a server or connection error, a timeout), the run waits (what the API asks, else 30 s)
and tries again, up to 5 times; a long wait (out of credits) stops the run, as does Ctrl-C, and
`run.yaml` lists what was not run. The exit code is 0 for a
run that went through, 1 for one that was refused or stopped early.

## Modeler runs

```sh
./vitality lab run modeler <evidence-dataset> [--label x] [--set effort=medium]   # also max_operations,
                                                                                  #   model, run_timeout_seconds
```

Runs on the VM, with the Claude login in `~/.claude/.credentials.json` (not the API key). The lab
records the run (`run.yaml` with `stage: modeler`, `playbook.md`, `changes.diff`), builds the image
(`docker build`), prepares the run's `model/` (the dataset's whole evidence copied in, waiting, plus its
`user-context.md`), prints the `docker run` command and runs ONE container: the run's `model/` at
`/model` and `records/` at `/state` (the paths the service will use), the login file, the `MODELER_*`
settings. The dataset, `run.yaml`, `playbook.md` and `changes.diff` are not mounted. Each session is
offered the oldest evidence not yet committed, up to the cap, and may read the rest. The container
commits to `model/` and writes `sessions.jsonl`, `transcripts/` and `replay.json` to `records/`; the lab then
finishes `run.yaml` (`result.sessions`, `cost_usd`, `stopped`, `not_offered`). Exit 0 if not stopped.
It refuses a captures dataset, a dataset without `user-context.md`, a missing login file and
untracked files under `modeler/` (among the watched folders). `observer` in turn refuses an
evidence dataset. `list` shows each dataset's kind, and `sessions <committed>/<total>` for modeler runs.

## Making a dataset

By hand: copy interaction folders, keeping their `<device>/<day>/<time>` path, from
`~/vitality-data/server/captures` into `datasets/<name>/captures/`, and write a `README.md` saying
how they were picked. Never change a dataset afterwards; make a new one instead.

### Making an evidence dataset

An evidence dataset is `datasets/<name>/observations/<device>/<day>/<time>.md` and
`operations/<device>/<day>/<time>.md` (copies of the server's files, same layout), a `user-context.md` (what the modeler is told about the user) and a `README.md`
saying how it was picked. Like a captures dataset, never change it afterwards.

## Reading and comparing runs

Open a run's `observations/` in order. To compare two runs on the same dataset:

```sh
diff -r runs/<first>/observations runs/<second>/observations
```

`run.yaml` says what differed between them (`commit`, `dirty`, `settings`, `prompt_version`).

## Development

```sh
uv run pytest -q
uv run pyright
```
