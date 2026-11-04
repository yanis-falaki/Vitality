#!/usr/bin/env python3
"""A stand-in for `claude` in tests; standard library only.

Reads a JSON list of steps from $FAKE_CLAUDE_SCRIPT and consumes one per invocation (the count is kept in
$FAKE_CLAUDE_SCRIPT.state). A step is {"write": {path: text}, "delete": [paths], "result": "...",
"is_error": false, "sleep": 0, "exit": 0, "no_result": false}; paths are relative to the working directory.
Each call appends {"argv", "api_key"} as a JSON line to $FAKE_CLAUDE_SCRIPT.calls.
"""

import json
import os
import sys
import time
from pathlib import Path


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
    print(json.dumps({"type": "system", "subtype": "init", "session_id": session}), flush=True)
    time.sleep(step.get("sleep", 0))
    for path, text in step.get("write", {}).items():
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    for path in step.get("delete", []):
        Path(path).unlink()
    if not step.get("no_result"):
        print(json.dumps({"type": "result", "subtype": "success", "session_id": session,
                          "is_error": step.get("is_error", False), "result": step.get("result", "done"),
                          "usage": {"input_tokens": 100, "output_tokens": 10}, "total_cost_usd": 0.5}), flush=True)
    return step.get("exit", 0)


if __name__ == "__main__":
    sys.exit(main())
