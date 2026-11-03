"""Writing the two evidence files of an observed interaction.

`observations/<name>.md` holds the transcript, `operations/<name>.md` the operations Claude
reported; both carry the interaction's local start and end.
"""

from vitality_protocol.messages import Interaction
from vitality_usermodel.formats import ObservationMeta, Operation, OperationsMeta, write_file

from vitality_observer.request import Reply, local_span
from vitality_observer.work import WorkItem, device_of


def write_evidence(interaction: WorkItem, captured: Interaction, reply: Reply) -> int:
    """Write the observation, then the operations file, replacing any earlier ones; return the number of operations.

    The operations come last, so an interaction counts as observed only once both are in place.
    Operation IDs are `op1`, `op2`, ... in the order of the reply.
    """
    start, end = local_span(captured)
    device = device_of(interaction.name)
    write_file(
        interaction.observation,
        ObservationMeta(interaction=interaction.name, device=device, start=start, end=end),
        reply.transcript.strip(),
    )
    operations = [
        Operation(id=f"op{number}", text=operation.text.strip(), confidence=operation.confidence)
        for number, operation in enumerate(reply.operations, start=1)
    ]
    write_file(
        interaction.operations,
        OperationsMeta(interaction=interaction.name, device=device, start=start, end=end, operations=operations),
    )
    return len(operations)
