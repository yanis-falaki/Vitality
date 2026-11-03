from pathlib import Path

from builders import make_interaction, make_item
from vitality_protocol.messages import Interaction
from vitality_usermodel.formats import ObservationMeta, OperationsMeta, read_file

from vitality_observer.request import Reply, ReplyOperation
from vitality_observer.writer import write_evidence


def reply(transcript: str = "Terminal open.", *texts: str) -> Reply:
    texts = texts or ("Opened a terminal", "Typed a command")
    return Reply(transcript=transcript, operations=[ReplyOperation(text=t, confidence=7) for t in texts])


def test_write_evidence(tmp_path: Path):
    interaction: Interaction = make_interaction("2026-11-03T14:22:05Z")
    item = make_item(tmp_path, interaction)

    count = write_evidence(item, interaction, reply())

    assert count == 2
    meta, body = read_file(item.observation, "observation")
    assert isinstance(meta, ObservationMeta)
    assert meta.interaction == "laptop/2026-11-03/14-22-05"
    assert meta.device == "laptop"
    assert meta.start.isoformat() == "2026-11-03T09:22:05-05:00"
    assert meta.end.isoformat() == "2026-11-03T09:22:12-05:00"
    assert "2026-11-03T09:22:05-05:00" in item.observation.read_text()
    assert body == "Terminal open."
    ops, _ = read_file(item.operations, "operations")
    assert isinstance(ops, OperationsMeta)
    assert [o.id for o in ops.operations] == ["op1", "op2"]
    assert [o.text for o in ops.operations] == ["Opened a terminal", "Typed a command"]
    assert ops.start == meta.start and ops.end == meta.end and ops.device == "laptop"


def test_rewrite_overwrites(tmp_path: Path):
    interaction = make_interaction("2026-11-03T14:22:05Z")
    item = make_item(tmp_path, interaction)

    write_evidence(item, interaction, reply("First.", "One", "Two", "Three"))
    count = write_evidence(item, interaction, reply("Second.", "Only"))

    assert count == 1
    _, body = read_file(item.observation, "observation")
    assert body == "Second."
    ops, _ = read_file(item.operations, "operations")
    assert isinstance(ops, OperationsMeta)
    assert [o.text for o in ops.operations] == ["Only"]
