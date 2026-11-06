import functools
import itertools
import json
import subprocess
from pathlib import Path

from test_replay import FAKE, FILES, action, dataset, prepared, script  # noqa: F401  (dataset is a fixture)
from vitality_modeler import repo
from vitality_modeler.config import ModelerConfig
from vitality_modeler.scheduler import Scheduler
from vitality_modeler.session import run_session


def test_run_models_backlog_then_idles(dataset: Path, tmp_path: Path):
    model, state = prepared(dataset, tmp_path / "run")
    state.mkdir()
    environ = script(tmp_path, [{"write": action(*FILES[:2])}, {"write": action(*FILES[2:])}])
    session = functools.partial(run_session, claude=str(FAKE), environ=environ)

    def sleep(seconds: float) -> None:
        if not repo.waiting(model):
            raise KeyboardInterrupt  # the backlog is done

    clock = itertools.count(0, 10_000).__next__  # every check is long after the last: the idle trigger fires
    s = Scheduler(model, state, ModelerConfig(max_operations=6), captures=None, session=session,
                  clock=lambda: float(clock()), sleep=sleep, log=lambda line: None)
    assert s.run() == 0
    log = subprocess.run(["git", "-C", str(model), "log", "--format=%s"], capture_output=True, text=True).stdout
    assert sum(line.startswith("modeler run ") for line in log.splitlines()) == 2
    # The first session starts after a quiet stretch; the second at once, on what the cap of 6 left.
    assert [json.loads(line)["trigger"] for line in (state / "sessions.jsonl").read_text().splitlines()] == \
        ["idle", "backlog"]
    assert json.loads((state / "status.json").read_text())["state"] == "idle"
