#!/usr/bin/env python3
"""A stand-in for `claude` in tests; standard library only.

Reads a JSON list of steps from $FAKE_CLAUDE_SCRIPT and consumes one per invocation (the count is kept in
$FAKE_CLAUDE_SCRIPT.state). A step is {"write": {path: text}, "delete": [paths], "result": "...",
"is_error": false, "sleep": 0, "exit": 0, "no_result": false, "api_error": null}; paths are relative to the
working directory. Each call appends {"argv", "api_key"} as a JSON line to $FAKE_CLAUDE_SCRIPT.calls.

`api_error` (a kind such as "rate_limit" or "billing_error") ends the turn as Claude Code 2.1.309 does on an
API error: a synthetic assistant message with `"error": <kind>` and `"is_api_error_message": true`, then an
error result with `"terminal_reason": "api_error"` and `api_error_status` (429 for "rate_limit").

Each invocation costs MODEL_USAGE (two models: the main loop and a sub-agent). The result's `modelUsage` and
`total_cost_usd` are cumulative over the session, as in Claude Code: a `--resume <id>` continues from the
totals of session <id> (kept in $FAKE_CLAUDE_SCRIPT.totals). `usage` is the main loop's, this invocation only.
"""

import json
import os
import sys
import time
from pathlib import Path

MODEL_USAGE = {
    "claude-main": {"inputTokens": 50, "outputTokens": 6, "cacheReadInputTokens": 30,
                    "cacheCreationInputTokens": 10, "costUSD": 0.4},
    "claude-sub": {"inputTokens": 10, "outputTokens": 4, "cacheReadInputTokens": 0,
                   "cacheCreationInputTokens": 0, "costUSD": 0.1},
}  # per invocation: 100 input tokens (cache included), 10 output, $0.50


def main() -> int:
    script = os.environ["FAKE_CLAUDE_SCRIPT"]
    steps = json.loads(Path(script).read_text())
    state = Path(script + ".state")
    n = int(state.read_text()) if state.exists() else 0
    state.write_text(str(n + 1))
    with open(script + ".calls", "a") as f:
        f.write(json.dumps({"argv": sys.argv[1:], "api_key": "ANTHROPIC_API_KEY" in os.environ}) + "\n")
    step = steps[min(n, len(steps) - 1)]
    session = f"fake-{n + 1}"
    totals_path = Path(script + ".totals")
    totals: dict[str, int] = json.loads(totals_path.read_text()) if totals_path.exists() else {}
    resumed = sys.argv[sys.argv.index("--resume") + 1] if "--resume" in sys.argv else None
    times = totals.get(resumed, 0) + 1 if resumed else 1
    totals[session] = times
    totals_path.write_text(json.dumps(totals))
    print(json.dumps({"type": "system", "subtype": "init", "session_id": session}), flush=True)
    time.sleep(step.get("sleep", 0))
    for path, text in step.get("write", {}).items():
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    for path in step.get("delete", []):
        Path(path).unlink()
    kind = step.get("api_error")
    text = step.get("result", "API Error" if kind else "done")
    if kind:
        print(json.dumps({"type": "assistant", "session_id": session, "parent_tool_use_id": None,
                          "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
                          "error": kind, "is_api_error_message": True}), flush=True)
    if not step.get("no_result"):
        model_usage = {m: {k: v * times for k, v in u.items()} for m, u in MODEL_USAGE.items()}
        result = {"type": "result", "subtype": "success", "session_id": session,
                  "is_error": bool(kind) or step.get("is_error", False), "result": text,
                  "usage": {"input_tokens": 50, "output_tokens": 6},
                  "modelUsage": model_usage, "total_cost_usd": round(0.5 * times, 6)}
        if kind:
            result |= {"terminal_reason": "api_error", "api_error_status": 429 if kind == "rate_limit" else None}
        print(json.dumps(result), flush=True)
    return step.get("exit", 0)


if __name__ == "__main__":
    sys.exit(main())
