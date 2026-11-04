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
    for word in ("segmentation", "activities", "strivings", "vitality-usermodel next-id",
                 "vitality-usermodel check", "user-context.md", "modeler-notes.md", "locked",
                 "conflicts_with", "established", "tentative"):
        assert word in PLAYBOOK, word
    assert "24 hours" not in PLAYBOOK
    assert "last revised" not in PLAYBOOK


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
