# usermodel

The shared package (`vitality_usermodel`) for the user model: the Markdown files, with YAML
frontmatter, that describe the owner at five levels (observation, operations, action, activity,
striving). The design is in `docs/superpowers/specs/2026-11-03-user-model-design.md`; the observer
writes the first two levels, and the modeler of a later phase writes the rest.

Modules:

- `formats.py` - one pydantic model per level's frontmatter, and `parse`, `render`, `read_file`
  and `write_file` for the files themselves.
- `ids.py` - ID patterns, `expected_path` (where a file of a kind and ID must live), and
  `next_id`, which issues `act-NNNN` / `str-NNNN` from the `.next-ids` counters.
- `check.py` - `check(model_dir, since=None)`, which lists every problem in a model folder (the
  rules below) as `Problem(path, message)`, without changing anything.
- `cli.py` - the `vitality-usermodel` command.
- `templates/` - `user-context.md` and `modeler-notes.md`, which `./vitality setup` copies into
  a new model folder (never over an existing file).

## The model folder

```
model/                a git repo
  observations/<device>/<YYYY-MM-DD>/<HH-MM-SS>.md   transcript, one per interaction
  operations/<device>/<YYYY-MM-DD>/<HH-MM-SS>.md     its operations
  actions/<YYYY-MM-DD>/<HH-MM-SS>.md                 by date only: actions may span devices
  activities/act-NNNN.md
  strivings/str-NNNN.md
  user-context.md                                    written by the owner
  modeler-notes.md                                   written by the modeler, for its next run
  .next-ids                                          last number issued per kind (JSON)
```

An interaction's name is `<device>/<YYYY-MM-DD>/<HH-MM-SS>`, with `-2`, `-3`... after the time
when two start in the same second. Names, paths and action IDs are UTC identifiers; every
timestamp inside a file is local time with its offset (ISO 8601). Times must carry an offset.

## Formats

Frontmatter fields are exact: unknown fields are rejected. `confidence` is an integer from 1 to 10.
Links point up: an operation never names its action, an action names its activities, an activity
its strivings.

| Level | File | Frontmatter | Body |
|---|---|---|---|
| observation | `observations/<interaction>.md` | `interaction`, `device`, `start`, `end` | the verbatim transcript |
| operations | `operations/<interaction>.md` | `interaction`, `device`, `start`, `end`, `operations`: a non-empty list of `{id: op1, text, confidence}` | none |
| action | `actions/<YYYY-MM-DD>/<HH-MM-SS>.md` | `id` (`<YYYY-MM-DD>/<HH-MM-SS>`), `summary`, `start`, `end`, `operations` (`<interaction>#<id>` references), `part_of` (activity IDs, may be empty), `confidence` | what was done, and why the operations were grouped |
| activity | `activities/act-NNNN.md` | `id`, `title`, `part_of` (striving IDs), `hinders` (striving IDs), `confidence`, `status` (`tentative` or `established`), `first_seen`, `last_seen`, `locked` | description and reasoning |
| striving | `strivings/str-NNNN.md` | `id`, `title`, `confidence`, `needs`, `orientation` (`approach`, `avoidance`, `mixed`), `aspiration_vs_obligation` (`ideal`, `ought`, `mixed`), `autonomy` (`autonomous`, `controlled`, `mixed`), `relations` (`conflicts_with`, `facilitates`, `serves`: striving IDs), `locked` | reasoning, evidence, and the identity link |

`needs` is any of `competence`, `autonomy`, `relatedness`, `status_recognition`, `self_coherence`,
`understanding`, `order_predictability`, `nurturance`, `safety_security`, `growth`, `stimulation`,
`purpose_meaning`. An operation is addressed as `<interaction>#<id>`, for example
`laptop/2026-11-03/14-22-05#op2`. IDs never change and are never reused; activities and strivings
take the next number from `.next-ids`. A file with `locked: true` is not changed by the modeler.

## The command

Run in the model folder or with `--model-dir DIR`:

- `vitality-usermodel check [--since REV]` prints `ok` (exit 0) or one `path: message` line per
  problem (exit 1). With `--since`, it also checks what changed in the working tree since that
  commit, untracked files included.
- `vitality-usermodel next-id activity|striving` issues and prints the next ID.

Errors (`--since` outside a git repository or naming no commit, a malformed `.next-ids` for
`next-id`) print `vitality-usermodel: <message>` to stderr and exit 1.

## The check

It lists every problem, not only the first. It fails when:

1. A file's frontmatter does not parse, misses a required field, or has a value outside its type or
   enum.
2. An ID does not match its format or its file path, is duplicated, or an ID present at `--since`
   was renamed. A new activity or striving number is above the value in `.next-ids`.
3. A link does not resolve: an operation reference to a missing file or ID, or `part_of`, `hinders`
   or `relations` naming a missing ID or the wrong kind.
4. An operation belongs to more than one action, or an operation in committed or staged evidence
   belongs to none. Evidence not yet staged (not offered to any run) is ignored.
5. An action's `start`/`end` lie outside the times of its operations' interactions.
6. With `--since`: a locked file changed or was deleted; a tracked observation or operations file
   changed or was deleted; `user-context.md` changed; or a file outside `actions/`, `activities/`,
   `strivings/`, `modeler-notes.md`, `.next-ids` changed, other than observation and operations
   files added since `--since`.

How the check reads rules the spec leaves open:

- Rule 2's "an ID present at `since` was renamed" is implemented as: a file that exists at
  `since` and still exists at the same path must keep its `id`; a vanished ID is a merge or
  deletion and is allowed (its links are caught by rule 3).
- "In committed or staged evidence" (rule 4) means listed by `git ls-files -- operations`. A
  folder that is not the top of a git repository has no such evidence, and `--since` fails there.
- A locked file (rule 6) is one with `locked: true` at `since`; clearing `locked` is a change to it.
- An action's times (rule 5) are compared with the earliest `start` and latest `end` of the
  operations files its references resolve to.

Develop with `uv run pytest -q` and `uv run pyright`.
