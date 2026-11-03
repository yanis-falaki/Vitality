# usermodel

The shared package (`vitality_usermodel`) for the user model: the Markdown files, with YAML
frontmatter, that describe the owner at five levels (observation, operations, action, activity,
striving). See `docs/superpowers/specs/2026-11-03-user-model-design.md` for the design.

Modules:

- `formats.py` - one pydantic model per level's frontmatter, and `parse`, `render`, `read_file`
  and `write_file` for the files themselves.
- `ids.py` - ID patterns, `expected_path` (where a file of a kind and ID must live), and
  `next_id`, which issues `act-NNNN` / `str-NNNN` from the `.next-ids` counters.
- `check.py` - `check(model_dir, since=None)`, which lists every problem in a model folder (the
  rules of the spec, section 9) as `Problem(path, message)`, without changing anything.
- `cli.py` - the `vitality-usermodel` command.

The command, run in the model folder or with `--model-dir DIR`:

- `vitality-usermodel check [--since REV]` prints `ok` (exit 0) or one `path: message` line per
  problem (exit 1). With `--since`, it also checks what changed in the working tree since that
  commit, untracked files included.
- `vitality-usermodel next-id activity|striving` issues and prints the next ID.

Errors (`--since` outside a git repository or naming no commit, a malformed `.next-ids` for
`next-id`) print `vitality-usermodel: <message>` to stderr and exit 1.

How the check reads rules the spec leaves open:

- Rule 2's "an ID present at `--since` was renamed" is implemented as: a file that exists at
  `since` and still exists at the same path must keep its `id`; a vanished ID is a merge or
  deletion and is allowed (its links are caught by rule 3).
- "In committed or staged evidence" (rule 4) means listed by `git ls-files -- operations`. A
  folder that is not the top of a git repository has no such evidence, and `--since` fails there.
- A locked file (rule 6) is one with `locked: true` at `since`; clearing `locked` is a change to it.
- An action's times (rule 5) are compared with the earliest `start` and latest `end` of the
  operations files its references resolve to.

Develop with `uv run pytest -q` and `uv run pyright`.
