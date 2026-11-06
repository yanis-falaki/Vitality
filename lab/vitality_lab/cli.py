"""`vitality-lab run observer <dataset> | list`: run a stage on a fixed dataset, and see what was run.

The lab folder is `$LAB_DIR` (default `~/vitality-lab`): `datasets/<name>/captures/` are copied
interactions, made by hand; `runs/<folder>/` are what runs wrote, never changed afterwards. The
design is in `docs/superpowers/specs/2026-11-04-lab-design.md`.
"""

import argparse
import asyncio
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from vitality_observer.config import ObserverConfig
from vitality_observer.provider.anthropic_api import AnthropicObserver
from vitality_observer.provider.interface import Observer
from vitality_observer.request import PROMPT, PROMPT_VERSION
from vitality_observer.work import interactions

from vitality_lab.modeler_run import dataset_kind, run_command, run_modeler_stage
from vitality_lab.observer_run import observer_settings, run_observer, settings_record
from vitality_lab.record import WATCHED, Refused, git_state, read_run_yaml, run_folder_name, write_run_yaml

REPO = Path(__file__).resolve().parents[2]
"""The Vitality checkout this code runs from: its git state is what a run records."""

def main(argv: Sequence[str] | None = None, *, environ: Mapping[str, str] = os.environ, repo: Path = REPO,
         make_observer: Callable[[ObserverConfig], Observer] = AnthropicObserver,
         now: Callable[[], datetime] = lambda: datetime.now().astimezone(),
         runner: Callable[[list[str]], int] = run_command, home: Path = Path.home()) -> int:
    """Run one `vitality-lab` command; return the exit code (0 done, 1 refused or stopped early, 2 usage)."""
    parser = argparse.ArgumentParser(prog="vitality-lab", description="Run a Vitality stage on a fixed dataset.")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="run a stage on a dataset")
    stages = run.add_subparsers(dest="stage", required=True)
    observer = stages.add_parser("observer", help="observe every interaction of a dataset")
    observer.add_argument("dataset", help="a folder name under $LAB_DIR/datasets")
    observer.add_argument("--label", help="a nickname appended to the run's folder name")
    observer.add_argument("--set", dest="overrides", action="append", default=[], metavar="NAME=VALUE",
                          help="override a setting for this run: model, effort, parallel, call_timeout_seconds")
    modeler = stages.add_parser("modeler", help="replay an evidence dataset through the modeler, in its container")
    modeler.add_argument("dataset", help="a folder name under $LAB_DIR/datasets")
    modeler.add_argument("--label", help="a nickname appended to the run's folder name")
    modeler.add_argument("--captures", action="store_true",
                         help="also give the sessions the dataset's screenshots (copied from the data folder)")
    modeler.add_argument("--set", dest="overrides", action="append", default=[], metavar="NAME=VALUE",
                         help="override a setting for this run: model, effort, max_operations, run_timeout_seconds")
    commands.add_parser("list", help="list datasets and runs")
    args = parser.parse_args(argv)

    lab = Path(environ.get("LAB_DIR") or Path.home() / "vitality-lab")
    if args.command == "list":
        print(listing(lab), end="")
        return 0
    try:
        if args.stage == "modeler":
            return run_modeler_stage(lab, args.dataset, args.label, args.overrides, environ, repo,
                                     runner=runner, now=now, home=home, captures=args.captures)
        return run_observer_stage(lab, args.dataset, args.label, args.overrides, environ, repo, make_observer, now)
    except Refused as e:
        print(f"vitality-lab: {e}", file=sys.stderr)
        return 1


def run_observer_stage(lab: Path, dataset: str, label: str | None, overrides: Sequence[str],
                       environ: Mapping[str, str], repo: Path,
                       make_observer: Callable[[ObserverConfig], Observer], now: Callable[[], datetime]) -> int:
    """Check, create the run folder, observe the dataset into it, record the result. Raises Refused before writing."""
    if dataset_kind(lab / "datasets" / dataset) == "evidence":
        raise Refused(f"{dataset} is an evidence dataset; use run modeler")
    captures = lab / "datasets" / dataset / "captures"
    try:
        config = observer_settings(environ, overrides)
    except ValueError as e:
        raise Refused(str(e)) from None
    if not interactions(replace(config, captures_dir=captures)):
        raise Refused(f"no interactions in {captures}")
    if not environ.get("ANTHROPIC_API_KEY", "").strip():
        raise Refused("ANTHROPIC_API_KEY is empty")
    try:
        state = git_state(repo, WATCHED)
    except RuntimeError as e:
        raise Refused(str(e)) from None
    if state.untracked:
        raise Refused("untracked files would run without being recorded; commit, ignore or remove them:\n  "
                      + "\n  ".join(state.untracked))
    started = now()
    try:
        folder = lab / "runs" / run_folder_name(started, state.commit, state.dirty, label)
    except ValueError as e:
        raise Refused(str(e)) from None

    folder.mkdir(parents=True)
    (folder / "prompt.md").write_text(PROMPT, encoding="utf-8")
    if state.dirty:
        (folder / "changes.diff").write_text(state.diff, encoding="utf-8")
    record: dict = {
        "stage": "observer",
        "input": f"datasets/{dataset}",
        "started": started.isoformat(timespec="seconds"),
        "finished": None,
        "commit": state.commit,
        "dirty": state.dirty,
        "label": label,
        "settings": settings_record(config),
        "prompt_version": PROMPT_VERSION,
        "result": None,
        "stopped": None,
        "failures": {},
        "not_run": [],
    }
    write_run_yaml(folder / "run.yaml", record)

    config = replace(config, captures_dir=captures, model_dir=folder, state_dir=folder)
    result = asyncio.run(run_observer(config, make_observer(config)))
    record.update({
        "finished": now().isoformat(timespec="seconds"),
        "result": {"ok": result.ok, "failed": len(result.failures), "not_run": len(result.not_run),
                   "cost_usd": round(result.cost_usd, 4)},
        "stopped": result.stopped,
        "failures": result.failures,
        "not_run": result.not_run,
    })
    write_run_yaml(folder / "run.yaml", record)

    print(f"\n{result.ok} ok, {len(result.failures)} failed, {len(result.not_run)} not run, ${result.cost_usd:.2f}")
    for name, detail in result.failures.items():
        print(f"  failed  {name}: {detail}")
    if result.stopped is not None:
        print(f"stopped early: {result.stopped}")
    print(folder)
    return 0 if result.stopped is None else 1


def listing(lab: Path) -> str:
    """The datasets (name, interaction count) and runs (folder, stage, input, result), oldest first."""
    lines = ["datasets:"]
    for folder in _folders(lab / "datasets"):
        kind = dataset_kind(folder)
        if kind == "evidence":
            count = sum(1 for p in (folder / "operations").rglob("*") if p.is_file())
            lines.append(f"  {folder.name}  {count} operations files  evidence")
        else:
            count = len(list((folder / "captures").glob("*/*/*/interaction.json")))
            lines.append(f"  {folder.name}  {count} interactions" + ("  captures" if kind else ""))
    lines.append("runs:")
    for folder in _folders(lab / "runs"):
        try:
            record = read_run_yaml(folder / "run.yaml")
        except Exception:   # unreadable or not YAML: still listed
            record = {}
        result = record.get("result")
        if isinstance(result, dict) and "sessions" in result:
            sessions = result["sessions"]
            summary = f"sessions {sessions.get('committed', 0)}/{sum(sessions.values())}  ${result['cost_usd']:.2f}"
        else:
            summary = (f"ok {result['ok']} failed {result['failed']}  ${result['cost_usd']:.2f}"
                       if isinstance(result, dict) else "running or crashed")
        lines.append(f"  {folder.name}  {record.get('stage', '?')}  {record.get('input', '?')}  {summary}")
    return "\n".join(lines) + "\n"


def _folders(path: Path) -> list[Path]:
    return sorted(p for p in path.iterdir() if p.is_dir()) if path.is_dir() else []
