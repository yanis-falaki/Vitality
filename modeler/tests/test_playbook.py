import re
from pathlib import Path

from vitality_modeler.playbook import PLAYBOOK, PLAYBOOK_VERSION, version
from vitality_modeler.replay import MODELER_NOTES_TEMPLATE
from vitality_usermodel.formats import Kind, parse

REPO = Path(__file__).parents[2]


def test_playbook_version_stable():
    assert re.fullmatch(r"[0-9a-f]{12}", PLAYBOOK_VERSION)
    assert version(PLAYBOOK) == PLAYBOOK_VERSION
    assert PLAYBOOK == (REPO / "modeler/vitality_modeler/playbook.md").read_text(encoding="utf-8")
    assert version(PLAYBOOK + "\nOne more line.\n") != PLAYBOOK_VERSION


def test_playbook_mentions():
    for word in ("actions sub-agent", "activities sub-agent", "strivings sub-agent", "vitality-usermodel next-id",
                 "vitality-usermodel check", "user-context.md", "modeler-notes.md", "locked",
                 "conflicts_with", "established", "tentative"):
        assert word in PLAYBOOK, word
    assert "24 hours" not in PLAYBOOK
    # Screenshots: where they are, how to find the right ones, and that they are used sparingly.
    flat_text = " ".join(PLAYBOOK.split())
    for phrase in ("<captures>/<interaction>/", "sparingly", "search the observations",
                   "pass the captures folder"):
        assert phrase in flat_text, phrase
    # Impersonal: the owner is "[Name]" in examples, never a real name.
    assert "Yanis" not in PLAYBOOK and "[Name]" in PLAYBOOK
    # One sub-agent per stage: no splitting a stage across several, no seams to join.
    assert "segmentation" not in PLAYBOOK.lower()
    for split in ("split the work", "seams"):
        assert split not in " ".join(PLAYBOOK.split()), split
    assert "last revised" not in PLAYBOOK
    flat = " ".join(PLAYBOOK.split())
    # No prior on how often strivings are revisited: the meaningful-change test alone decides.
    for biased in ("Strivings change slowly", "most sessions skip", "rarely revisit"):
        assert biased not in flat, biased
    # Fields: only unknown ones are rejected (several have defaults); write every field listed.
    assert "a missing or unknown field fails the check" not in flat
    assert "Unknown fields are rejected; write every field listed." in flat
    # Action ID suffix: the next free number in the one `-N` suffix, never a second suffix.
    assert "never `-2-2`" in flat
    # Work happens in the model folder itself; a worktree's changes would never be checked or committed.
    assert "never use worktree isolation" in flat


def test_playbook_examples_parse():
    # The example of each kind in the playbook is a valid file of that kind.
    found: list[Kind] = []
    for example in re.findall(r"```markdown\n(.*?)```", PLAYBOOK, re.DOTALL):
        match = re.search(r"^id: (\S+)", example, re.MULTILINE)
        assert match, example
        ident = match.group(1)
        kind: Kind = "activity" if ident.startswith("act-") else "striving" if ident.startswith("str-") else "action"
        parse(example, kind)
        found.append(kind)
    assert sorted(found) == ["action", "activity", "striving"]


def test_notes_template_matches_usermodel():
    assert MODELER_NOTES_TEMPLATE == (REPO / "usermodel/templates/modeler-notes.md").read_text(encoding="utf-8")
