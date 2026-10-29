"""The Vitality observer: turns each captured interaction into a Markdown observation.

It watches the server's captures, hands batches of unobserved interactions to Claude Code, and
keeps the resulting observations in the user model.

main.py     the program (`vitality-observer run | status`): loads the settings, then either
            runs the loop and the watcher on the captures directory, or shows the status
status.py   `status`: observed / total, skipped, and the last Claude call, in one line
loop.py     the loop: finds unobserved interactions, makes one Claude call at a time,
            retries failures, waits out usage limits, skips interactions that keep failing
claude.py   one `claude -p` call for a batch, and the line it adds to `calls.jsonl`
work.py     which interactions are unobserved (oldest first), the context for a call, the
            skipped list
config.py   the settings, read from `OBSERVER_*` environment variables
prompt.md   the instructions given to Claude with every call

Dependencies point down this list: `main` uses `loop`, `status`, and `claude` for the real Claude Code
(`ClaudeCode`); `loop` uses `claude` and `work`; `claude` uses `work`; `status` uses `work`;
all use `config`.
"""
