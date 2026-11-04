"""The pure pieces around one Claude Code session: its command line, environment and message, and its output.

Nothing here starts a process; the caller runs `claude_command(...)` with `session_environment(...)`
in the model folder and feeds the stdout lines to `read_stream`.
"""

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from vitality_modeler.config import ModelerConfig
from vitality_usermodel.formats import ActivityMeta, StrivingMeta, read_file

LIMIT_RE = re.compile(r"usage limit|limit (reached|exceeded)|out of (usage|credits)", re.IGNORECASE)
"""Matches the text of a result that failed because the subscription's usage ran out."""

_SECRETS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")


def claude_command(message: str, playbook: str, config: ModelerConfig, *,
                   resume: str | None = None, claude: str = "claude") -> list[str]:
    """The argv of one session: print mode, the playbook appended to the system prompt, project settings only,
    no ambient MCP servers, permissions bypassed, streamed JSON. `resume` continues that session."""
    argv = [claude, "-p", message, "--append-system-prompt", playbook,
            "--model", config.model, "--effort", config.effort,
            "--setting-sources", "project", "--strict-mcp-config",
            "--permission-mode", "bypassPermissions",
            "--output-format", "stream-json", "--verbose"]
    if resume is not None:
        argv += ["--resume", resume]
    return argv


def session_environment(environ: Mapping[str, str]) -> dict[str, str]:
    """A copy of `environ` without the API credentials, so the session uses the subscription login."""
    return {k: v for k, v in environ.items() if k not in _SECRETS}


def run_message(offered: list[tuple[str, datetime]], activities: list[str], strivings: list[str],
                captures: str | None) -> str:
    """The message of a run: the offered operations files (path and start, oldest first), the index of
    activities and strivings (`model_index` lines; "none yet" when empty), the instruction to read the two
    context files, and the captures folder when there is one.

    Callers pass each file's frontmatter `start` as read (aware, with the device's offset); it is printed in
    that offset, unconverted, so times of day are the user's local ones. Ordering is by instant."""
    lines = ["Operations files offered to this run (oldest first):"]
    for path, start in sorted(offered, key=lambda o: o[1]):
        lines.append(f"- {path} (starts {start.isoformat(timespec="minutes")})")
    lines += ["", "Activities:", *(activities or ["none yet"]),
              "", "Strivings:", *(strivings or ["none yet"]),
              "", "Read user-context.md and modeler-notes.md first."]
    if captures is not None:
        lines += ["", f"Captures folder: {captures}"]
    return "\n".join(lines)


def model_index(model: Path) -> tuple[list[str], list[str]]:
    """One line per activity and per striving, by file name:
    `<id>  <title>  <status or "">  part_of: <ids, comma-separated>`. Strivings have no status or `part_of`."""
    activities: list[str] = []
    for path in sorted((model / "activities").glob("act-*.md")):
        meta, _ = read_file(path, "activity")
        assert isinstance(meta, ActivityMeta)
        activities.append(f"{meta.id}  {meta.title}  {meta.status}  part_of: {', '.join(meta.part_of)}")
    strivings: list[str] = []
    for path in sorted((model / "strivings").glob("str-*.md")):
        meta, _ = read_file(path, "striving")
        assert isinstance(meta, StrivingMeta)
        strivings.append(f"{meta.id}  {meta.title}    part_of: ")
    return activities, strivings


@dataclass(frozen=True)
class StreamResult:
    """What a session's output says happened."""

    session_id: str | None
    is_error: bool
    text: str
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: float | None
    limited: bool       # failed because usage ran out


def read_stream(lines: Iterable[str]) -> StreamResult:
    """Parse `--output-format stream-json` lines.

    The session id is the first `session_id` seen; the last `result` event gives the outcome. With no result
    event the run is an error ("no result in the stream"). Lines that are not JSON objects are skipped.
    """
    session_id: str | None = None
    result: dict[str, Any] | None = None
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if session_id is None and isinstance(event.get("session_id"), str):
            session_id = event["session_id"]
        if event.get("type") == "result":
            result = event
    if result is None:
        return StreamResult(session_id, True, "no result in the stream", None, None, None, False)
    is_error = bool(result.get("is_error"))
    text = str(result.get("result") or "")
    raw = result.get("usage")
    usage: dict[str, Any] = raw if isinstance(raw, dict) else {}
    tokens = [usage.get(k) if isinstance(usage.get(k), int) else None for k in ("input_tokens", "output_tokens")]
    cost = result.get("total_cost_usd")
    return StreamResult(session_id, is_error, text, tokens[0], tokens[1],
                        float(cost) if isinstance(cost, (int, float)) and not isinstance(cost, bool) else None,
                        is_error and LIMIT_RE.search(text) is not None)
