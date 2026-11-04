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
from typing import Any, cast

from vitality_modeler.config import ModelerConfig
from vitality_usermodel.formats import ActivityMeta, StrivingMeta, read_file

LIMIT_RE = re.compile(r"^\s*(You've hit your|You've reached your|You're out of)\b")
"""Matches the text of a result that failed because the subscription's usage ran out: the openings of
Claude Code's own limit messages (2.1.309: "You've hit your session limit · resets 3pm", "You've reached
your …", "You're out of extra usage"). A fallback; the typed error of the stream is read first."""

LIMIT_ERRORS = ("rate_limit", "billing_error")
"""Kinds of the `error` of a synthetic assistant message (`is_api_error_message: true`) meaning the usage ran out."""

_SECRETS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")


def claude_command(message: str, playbook: str, config: ModelerConfig, *,
                   resume: str | None = None, claude: str = "claude") -> list[str]:
    """The argv of one session: print mode, the playbook appended to the system prompt, project settings only,
    no ambient MCP servers, permissions bypassed, streamed JSON with the sub-agents' text.
    `resume` continues that session."""
    argv = [claude, "-p", message, "--append-system-prompt", playbook,
            "--model", config.model, "--effort", config.effort,
            "--setting-sources", "project", "--strict-mcp-config",
            "--permission-mode", "bypassPermissions",
            "--output-format", "stream-json", "--verbose", "--forward-subagent-text"]
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

    An error result is `limited` when the main loop (no `parent_tool_use_id`) ended on a synthetic API-error
    message whose `error` is one of `LIMIT_ERRORS`, when the result has `terminal_reason: "api_error"` with
    `api_error_status` 429, or, failing those, when its text matches `LIMIT_RE`.

    Tokens and cost come from the result's `modelUsage`, which covers sub-agents too (the result's `usage` is
    the main loop only): input tokens are input plus cache read plus cache creation, summed over models;
    output tokens are summed; cost is `total_cost_usd`, or the sum of `costUSD` when that is absent. Without
    `modelUsage`, `usage` is read the same way. Claude Code 2.1.309 documents these totals as cumulative over
    a resumed session (the resumed run's result already carries the earlier runs); to be confirmed on the
    first real run.
    """
    session_id: str | None = None
    result: dict[str, Any] | None = None
    typed_limit = False
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        event = cast(dict[str, Any], event)
        if session_id is None and isinstance(event.get("session_id"), str):
            session_id = event["session_id"]
        if event.get("type") == "result":
            result = event
        elif (event.get("type") == "assistant" and event.get("is_api_error_message") is True
              and event.get("parent_tool_use_id") is None and event.get("error") in LIMIT_ERRORS):
            typed_limit = True
    if result is None:
        return StreamResult(session_id, True, "no result in the stream", None, None, None, False)
    is_error = bool(result.get("is_error"))
    text = str(result.get("result") or "")
    status_limit = result.get("terminal_reason") == "api_error" and result.get("api_error_status") == 429
    input_tokens, output_tokens, cost_sum = _usage(result)
    cost = _number(result.get("total_cost_usd"))
    return StreamResult(session_id, is_error, text, input_tokens, output_tokens,
                        cost if cost is not None else cost_sum,
                        is_error and (typed_limit or status_limit or LIMIT_RE.search(text) is not None))


def _usage(result: dict[str, Any]) -> tuple[int | None, int | None, float | None]:
    # Input tokens (cache included), output tokens and the costUSD sum, from modelUsage or else usage.
    raw = result.get("modelUsage")
    if isinstance(raw, dict) and raw:
        models = [cast(dict[str, Any], m) for m in cast(dict[str, Any], raw).values() if isinstance(m, dict)]
        keys = ("inputTokens", "cacheReadInputTokens", "cacheCreationInputTokens"), ("outputTokens",)
        costs = [_number(m.get("costUSD")) for m in models]
        return (_total(models, keys[0]), _total(models, keys[1]),
                None if not costs or None in costs else sum(c for c in costs if c is not None))
    raw = result.get("usage")
    usage = [cast(dict[str, Any], raw)] if isinstance(raw, dict) else []
    return (_total(usage, ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")),
            _total(usage, ("output_tokens",)), None)


def _total(entries: list[dict[str, Any]], keys: tuple[str, ...]) -> int | None:
    # The sum of `keys` over `entries`; None when an entry lacks the first key as an int (the others may be absent).
    total = 0
    for entry in entries:
        values = [entry.get(k, 0 if i else None) for i, k in enumerate(keys)]
        if not all(isinstance(v, int) and not isinstance(v, bool) for v in values):
            return None
        total += sum(cast(list[int], values))
    return total if entries else None


def _number(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None
