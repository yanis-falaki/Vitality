"""The modeler stage of a lab run: its settings, the docker commands, and finishing the record.

The host side only records the run and starts ONE container (`docker run`); the replay itself is
the modeler's own (`vitality-modeler replay /dataset /run`). What the container wrote in the run
folder (`replay.json`, `sessions.jsonl`) is what `run.yaml` is finished from.
"""

import json
import os
import signal
import subprocess
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Literal

from vitality_modeler.config import SETTINGS, ModelerConfig, load_modeler_config
from vitality_modeler.playbook import PLAYBOOK, PLAYBOOK_VERSION

from vitality_lab.record import WATCHED, Refused, git_state, run_folder_name, write_run_yaml

IMAGE = "vitality-modeler"
CREDENTIALS_IN_CONTAINER = "/home/modeler/.claude/.credentials.json"


def dataset_kind(dataset: Path) -> Literal["captures", "evidence"] | None:
    """`captures` if `captures/` holds interactions, `evidence` if `operations/` holds files, else None."""
    if any((dataset / "captures").glob("*/*/*/interaction.json")):
        return "captures"
    operations = dataset / "operations"
    if operations.is_dir() and any(p.is_file() for p in operations.rglob("*")):
        return "evidence"
    return None


def modeler_settings(environ: Mapping[str, str], overrides: Sequence[str]) -> ModelerConfig:
    """The modeler settings for a run: `MODELER_<SETTING>` from `environ`, then `<setting>=<value>` overrides.

    Raises ValueError for an override without "=", a name not in `SETTINGS`, or a value the modeler rejects.
    """
    env = {f"MODELER_{name.upper()}": environ[f"MODELER_{name.upper()}"]
           for name in SETTINGS if f"MODELER_{name.upper()}" in environ}
    for override in overrides:
        name, equals, value = override.partition("=")
        if not equals:
            raise ValueError(f"a setting is <name>=<value>, got {override!r}")
        if name not in SETTINGS:
            raise ValueError(f"unknown setting {name!r}; one of {', '.join(SETTINGS)}")
        env[f"MODELER_{name.upper()}"] = value
    return load_modeler_config(env)


def settings_record(config: ModelerConfig) -> dict:
    return {name: getattr(config, name) for name in SETTINGS}


def docker_build_command(repo: Path) -> list[str]:
    return ["docker", "build", "-f", str(repo / "modeler/Dockerfile"), "-t", IMAGE, str(repo)]


def docker_run_command(dataset: Path, run: Path, credentials: Path, config: ModelerConfig,
                       uid: int, gid: int) -> list[str]:
    """The one container: dataset read-only, the run folder writable, the login file, the settings.

    No API key is passed: the session uses the owner's Claude login.
    """
    env = [arg for name in SETTINGS for arg in ("-e", f"MODELER_{name.upper()}={getattr(config, name)}")]
    return ["docker", "run", "--rm", "--user", f"{uid}:{gid}",
            "-v", f"{dataset}:/dataset:ro", "-v", f"{run}:/run",
            "-v", f"{credentials}:{CREDENTIALS_IN_CONTAINER}",
            *env, IMAGE, "vitality-modeler", "replay", "/dataset", "/run"]


def run_command(command: list[str]) -> int:
    """Print `command`, run it, return its exit code."""
    print("$", " ".join(command), flush=True)
    # Ctrl-C reaches `docker run` too, which passes it to the container; we only wait for it to finish.
    previous = signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        return subprocess.Popen(command).wait()
    finally:
        signal.signal(signal.SIGINT, previous)


def run_modeler_stage(lab: Path, dataset: str, label: str | None, overrides: Sequence[str],
                      environ: Mapping[str, str], repo: Path, *,
                      runner: Callable[[list[str]], int] = run_command,
                      now: Callable[[], datetime], home: Path = Path.home()) -> int:
    """Check, create the run folder, run the container, finish the record. Raises Refused before writing.
    """
    folder_in_lab = lab / "datasets" / dataset
    kind = dataset_kind(folder_in_lab)
    if kind == "captures":
        raise Refused(f"{dataset} is a captures dataset; use run observer")
    if kind is None:
        raise Refused(f"{folder_in_lab} has no operations to replay")
    if not (folder_in_lab / "user-context.md").is_file():
        raise Refused(f"{folder_in_lab} has no user-context.md")
    credentials = home / ".claude" / ".credentials.json"
    if not credentials.is_file():
        raise Refused(f"{credentials} is missing; log in to Claude Code on this machine first")
    try:
        config = modeler_settings(environ, overrides)
    except ValueError as e:
        raise Refused(str(e)) from None
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
    (folder / "playbook.md").write_text(PLAYBOOK, encoding="utf-8")
    if state.dirty:
        (folder / "changes.diff").write_text(state.diff, encoding="utf-8")
    record: dict = {
        "stage": "modeler",
        "input": f"datasets/{dataset}",
        "started": started.isoformat(timespec="seconds"),
        "finished": None,
        "commit": state.commit,
        "dirty": state.dirty,
        "label": label,
        "settings": settings_record(config),
        "playbook_version": PLAYBOOK_VERSION,
        "result": None,
        "stopped": None,
        "container_exit": None,
        "not_offered": [],
    }
    write_run_yaml(folder / "run.yaml", record)

    def give_up(reason: str) -> int:
        record.update(finished=now().isoformat(timespec="seconds"), stopped=reason)
        write_run_yaml(folder / "run.yaml", record)
        print(f"stopped early: {reason}\n{folder}")
        return 1

    try:
        built = runner(docker_build_command(repo))
    except OSError as e:
        return give_up(f"docker could not be run: {e}")
    if built != 0:
        return give_up("image build failed")
    try:
        exit_code = runner(docker_run_command(folder_in_lab.resolve(), folder.resolve(), credentials, config,
                                              os.getuid(), os.getgid()))
    except OSError as e:
        return give_up(f"docker could not be run: {e}")
    record["container_exit"] = exit_code

    summary = _read_summary(folder)
    record["finished"] = now().isoformat(timespec="seconds")
    if summary is None:
        record["stopped"] = "container ended without a summary"
    else:
        cost = _total_cost(folder / "sessions.jsonl")
        record.update(result={"sessions": summary["sessions"], "cost_usd": round(cost, 4)},
                      stopped=summary["stopped"], not_offered=summary["not_offered"])
        if exit_code != 0 and record["stopped"] is None:
            record["stopped"] = f"container exited with {exit_code}"
    write_run_yaml(folder / "run.yaml", record)

    if summary is not None:
        print(f"\nsessions {summary['sessions']}, ${record['result']['cost_usd']:.2f}")
    if record["stopped"] is not None:
        print(f"stopped early: {record['stopped']}")
    print(folder)
    return 0 if record["stopped"] is None else 1


def _read_summary(folder: Path) -> dict | None:
    try:
        data = json.loads((folder / "replay.json").read_text(encoding="utf-8"))
        return {"sessions": dict(data["sessions"]), "stopped": data.get("stopped"),
                "not_offered": list(data.get("not_offered") or [])}
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _total_cost(path: Path) -> float:
    total = 0.0
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return 0.0
    for line in lines:
        try:
            total += json.loads(line).get("cost") or 0
        except (ValueError, AttributeError):
            continue
    return total
