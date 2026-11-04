from datetime import datetime
from pathlib import Path

import pytest
from vitality_modeler.repo import init_model
from vitality_usermodel.formats import ObservationMeta, Operation, OperationsMeta, write_file


@pytest.fixture
def model(tmp_path: Path) -> Path:
    folder = tmp_path / "model"
    folder.mkdir()
    init_model(folder, "context\n", "notes\n")
    return folder


def add_evidence(model: Path, name: str, start_iso: str, n_ops: int) -> None:
    """Write operations/<name>.md (with `n_ops` operations) and observations/<name>.md; `name` is `device/date/time`."""
    start = datetime.fromisoformat(start_iso)
    device = name.split("/")[0]
    ops = [Operation(id=f"op{i + 1}", text=f"did {i + 1}", confidence=5) for i in range(n_ops)]
    write_file(model / "operations" / f"{name}.md", OperationsMeta(
        interaction=name, device=device, start=start, end=start, operations=ops))
    write_file(model / "observations" / f"{name}.md", ObservationMeta(
        interaction=name, device=device, start=start, end=start), "observed")
