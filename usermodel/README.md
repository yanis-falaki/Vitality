# usermodel

The shared package (`vitality_usermodel`) for the user model: the Markdown files, with YAML
frontmatter, that describe the owner at five levels (observation, operations, action, activity,
striving). See `docs/superpowers/specs/2026-11-03-user-model-design.md` for the design.

Modules:

- `formats.py` - one pydantic model per level's frontmatter, and `parse`, `render`, `read_file`
  and `write_file` for the files themselves.

Develop with `uv run pytest -q` and `uv run pyright`.
