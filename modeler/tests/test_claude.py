import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from vitality_modeler.claude import (
    LIMIT_RE, claude_command, model_index, read_stream, run_message, session_environment)
from vitality_modeler.config import ModelerConfig
from vitality_usermodel.formats import ActivityMeta, StrivingMeta, write_file

FAKE = Path(__file__).parent / "fake_claude.py"
T = datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc)


def test_command_exact():
    cfg = ModelerConfig(model="m1", effort="max")
    base = ["claude", "-p", "msg", "--append-system-prompt", "play", "--model", "m1", "--effort", "max",
            "--setting-sources", "project", "--strict-mcp-config", "--permission-mode", "bypassPermissions",
            "--output-format", "stream-json", "--verbose"]
    assert claude_command("msg", "play", cfg) == base
    assert claude_command("msg", "play", cfg, resume="s1", claude="/x/claude") == ["/x/claude", *base[1:], "--resume", "s1"]


def test_environment_drops_key():
    env = session_environment({"ANTHROPIC_API_KEY": "k", "ANTHROPIC_AUTH_TOKEN": "t", "HOME": "/h"})
    assert env == {"HOME": "/h"}


def test_run_message():
    later = datetime(2026, 11, 2, 18, 30, tzinfo=timezone.utc)
    msg = run_message([("operations/b.md", later), ("operations/a.md", T)], [], [], None)
    assert msg.index("operations/a.md") < msg.index("operations/b.md")
    assert msg.count("none yet") == 2
    assert "Read user-context.md and modeler-notes.md first." in msg
    assert "captures" not in msg.lower()
    full = run_message([("operations/a.md", T)], ["act-0001  X  tentative  part_of: "], ["str-0001  Y    part_of: "], "captures/")
    assert "act-0001  X" in full and "str-0001  Y" in full and "none yet" not in full
    assert "captures/" in full


def test_model_index(model: Path):
    write_file(model / "activities/act-0001.md", ActivityMeta(
        id="act-0001", title="Running", part_of=["str-0001", "str-0002"], confidence=5,
        status="tentative", first_seen=T, last_seen=T))
    write_file(model / "strivings/str-0001.md", StrivingMeta(
        id="str-0001", title="Health", confidence=5, needs=["autonomy"], orientation="approach",
        aspiration_vs_obligation="ideal", autonomy="autonomous"))
    acts, strs = model_index(model)
    assert acts == ["act-0001  Running  tentative  part_of: str-0001, str-0002"]
    assert strs == ["str-0001  Health    part_of: "]


def _result(**kw):
    return json.dumps({"type": "result", **kw})


def test_read_stream_success():
    r = read_stream([
        json.dumps({"type": "system", "subtype": "init", "session_id": "s9"}),
        _result(is_error=False, result="done", usage={"input_tokens": 100, "output_tokens": 10}, total_cost_usd=0.5),
    ])
    assert (r.session_id, r.is_error, r.text, r.input_tokens, r.output_tokens, r.cost_usd, r.limited) == \
        ("s9", False, "done", 100, 10, 0.5, False)


def test_read_stream_error_limit():
    r = read_stream([_result(is_error=True, result="Claude AI usage limit reached")])
    assert r.is_error and r.limited and r.input_tokens is None
    assert not read_stream([_result(is_error=True, result="boom")]).limited
    assert LIMIT_RE.search("Out of credits")


def test_read_stream_no_result():
    r = read_stream([json.dumps({"type": "system", "session_id": "s"})])
    assert (r.session_id, r.is_error, r.text, r.cost_usd, r.limited) == ("s", True, "no result in the stream", None, False)


def test_read_stream_skips_garbage():
    r = read_stream(["not json", "", "[1]", _result(is_error=False, result="ok")])
    assert r.text == "ok" and not r.is_error


def test_fake_claude_runs(tmp_path: Path):
    script = tmp_path / "script.json"
    script.write_text(json.dumps([{"write": {"activities/x.md": "hi"}, "result": "fine"}]))
    env = {**os.environ, "FAKE_CLAUDE_SCRIPT": str(script), "ANTHROPIC_API_KEY": "k"}
    work = tmp_path / "work"
    work.mkdir()
    p = subprocess.run([sys.executable, str(FAKE), "-p", "hello"], cwd=work, env=env, capture_output=True, text=True)
    assert p.returncode == 0
    assert (work / "activities/x.md").read_text() == "hi"
    r = read_stream(p.stdout.splitlines())
    assert (r.session_id, r.text, r.is_error, r.cost_usd) == ("fake-1", "fine", False, 0.5)
    calls = Path(f"{script}.calls").read_text().splitlines()
    assert json.loads(calls[0])["argv"] == ["-p", "hello"] and json.loads(calls[0])["api_key"] is True


def test_run_message_keeps_own_offsets():
    from datetime import timedelta
    west = datetime(2026, 10, 26, 18, 30, tzinfo=timezone(timedelta(hours=-4)))   # 22:30 UTC
    east = datetime(2026, 10, 26, 23, 45, tzinfo=timezone(timedelta(hours=2)))    # 21:45 UTC
    msg = run_message([("operations/w.md", west), ("operations/e.md", east)], [], [], None)
    assert "2026-10-26T18:30-04:00" in msg and "2026-10-26T23:45+02:00" in msg
    assert msg.index("operations/e.md") < msg.index("operations/w.md")


def test_read_stream_malformed_result():
    r = read_stream([_result(is_error=False, result="ok", usage=[], total_cost_usd="x")])
    assert (r.text, r.input_tokens, r.output_tokens, r.cost_usd) == ("ok", None, None, None)
    r = read_stream([_result(is_error=False, result="ok", usage={"input_tokens": "1", "output_tokens": 2}, total_cost_usd=1)])
    assert (r.input_tokens, r.output_tokens, r.cost_usd) == (None, 2, 1.0)
