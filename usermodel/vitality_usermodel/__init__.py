"""The Vitality user model: the Markdown files that describe the owner, and the code to handle them.

formats.py  the frontmatter of every level (observation, operations, action, activity,
            striving) as pydantic models, and parsing, rendering and atomic writing of the files
ids.py      ID formats, the path each file must have, and next-id allocation
check.py    the check of a model folder against every rule of the spec, section 9
cli.py      the `vitality-usermodel` command: `check` and `next-id`
"""
