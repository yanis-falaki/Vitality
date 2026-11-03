"""The Vitality observer: turns each captured interaction into a Markdown observation.

It watches the server's captures, sends each unobserved interaction to the Anthropic API, and
keeps the resulting observation and operations files in the user model.

main.py     the program (`vitality-observer run | status`): loads the settings, then either
            runs the loop with the Anthropic API provider and the watcher on the captures
            directory (refusing without `ANTHROPIC_API_KEY`), or shows the status
status.py   `status`: observed / total, skipped, and the last API call, in one line
loop.py     the loop: hands the oldest unobserved interactions to the provider in rounds
            (`parallel` in direct mode, up to 100 in batch mode), waits out credit and rate
            limits, backs off on failures, skips interactions that keep failing
provider/   the model provider behind the `Observer` interface (`interface.py`):
            `anthropic_api.py` makes one API call per interaction, direct or as a batch,
            writes the files of each good reply and adds a line per interaction to `calls.jsonl`
request.py  the API request for one interaction (prompt, reply schema, images) and parsing
            the reply
writer.py   writes the observation and operations files for a reply
work.py     which interactions are unobserved (oldest first across devices), the skipped list
config.py   the settings, read from `OBSERVER_*` environment variables
prompt.md   the instructions given to Claude with every call

Dependencies point down this list: `main` uses `loop`, `status`, and `provider` for the real API
(`AnthropicObserver`); `loop` uses `provider` and `work`; `provider` uses `writer`, `request` and
`work`; `status` uses `work`; `writer` uses `request`; `request` uses `work`; all use `config`.
"""
