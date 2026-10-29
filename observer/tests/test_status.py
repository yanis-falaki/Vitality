import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from vitality_observer import status as status_module
from vitality_observer.config import ObserverConfig
from vitality_observer.status import status

from test_work import capture, observe


@pytest.fixture(autouse=True)
def utc_clock(monkeypatch):
    """Local time is UTC and 'now' is 2026-10-29 22:00, so output does not depend on the machine."""
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    monkeypatch.setattr(status_module, "_now", lambda: datetime(2026, 10, 29, 22, 0, tzinfo=UTC))
    yield
    monkeypatch.undo()
    time.tzset()


@pytest.fixture
def cfg(tmp_path: Path) -> ObserverConfig:
    config = ObserverConfig(
        captures_dir=tmp_path / "captures",
        model_dir=tmp_path / "model",
        state_dir=tmp_path / "state",
    )
    for name in ("laptop/2026-10-29/10-00-00", "laptop/2026-10-29/10-05-00", "laptop/2026-10-29/10-10-00"):
        capture(config, name)
    return config


def log_calls(cfg: ObserverConfig, *records: dict) -> None:
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    (cfg.state_dir / "calls.jsonl").write_text("".join(json.dumps(r) + "\n" for r in records))


def call(time: str = "2026-10-29T21:28:26Z", ok: bool = True, detail: str = "", duration_ms: int = 40_000) -> dict:
    return {"time": time, "interactions": [], "exit": 0, "ok": ok, "detail": detail, "duration_ms": duration_ms}


def test_counts_and_last_call(cfg, capsys):
    observe(cfg, "laptop/2026-10-29/10-00-00")
    (cfg.state_dir).mkdir()
    (cfg.state_dir / "skipped.txt").write_text("laptop/2026-10-29/10-05-00\tkept failing\n")
    log_calls(cfg, call("2026-10-29T20:00:00Z", duration_ms=5_000), call())
    assert status(cfg) == 0
    assert capsys.readouterr().out == "observer: 1 / 3 observed · 1 skipped · last call 21:28:26 UTC ok (40 s)\n"


def test_an_empty_observation_does_not_count_as_observed(cfg, capsys):
    observe(cfg, "laptop/2026-10-29/10-00-00", text="")
    status(cfg)
    assert capsys.readouterr().out.startswith("observer: 0 / 3 observed · 0 skipped")


def test_failed_last_call_shows_the_detail(cfg, capsys):
    log_calls(cfg, call(ok=False, detail="timed out after 600 s", duration_ms=600_400))
    assert status(cfg) == 0
    assert capsys.readouterr().out.endswith("last call 21:28:26 UTC failed: timed out after 600 s (600 s)\n")


def test_a_long_detail_is_cut_to_60_characters(cfg, capsys):
    log_calls(cfg, call(ok=False, detail="x" * 100))
    status(cfg)
    out = capsys.readouterr().out
    assert f"failed: {'x' * 59}… (" in out


def test_no_calls_yet(cfg, capsys):
    assert status(cfg) == 0
    assert capsys.readouterr().out == "observer: 0 / 3 observed · 0 skipped · no calls yet\n"


def test_an_empty_log_is_no_calls_yet(cfg, capsys):
    log_calls(cfg)
    status(cfg)
    assert capsys.readouterr().out.endswith("no calls yet\n")


def test_last_call_on_an_earlier_day_shows_the_date(cfg, capsys):
    log_calls(cfg, call("2026-10-27T09:05:59Z"))
    status(cfg)
    assert capsys.readouterr().out.endswith("last call 2026-10-27 09:05 UTC ok (40 s)\n")


def test_last_call_is_shown_in_local_time(cfg, capsys, monkeypatch):
    monkeypatch.setenv("TZ", "America/Vancouver")  # UTC-7 in October
    time.tzset()
    log_calls(cfg, call("2026-10-29T21:28:26Z"))
    status(cfg)
    assert capsys.readouterr().out.endswith("last call 14:28:26 PDT ok (40 s)\n")


def test_only_the_end_of_a_large_log_is_read(cfg, capsys):
    log_calls(cfg, *[call(detail="y" * 200) for _ in range(1000)], call("2026-10-29T21:30:00Z", duration_ms=1_600))
    status(cfg)
    assert capsys.readouterr().out.endswith("last call 21:30:00 UTC ok (2 s)\n")


def test_a_trailing_blank_line_is_ignored(cfg, capsys):
    log_calls(cfg, call())
    with open(cfg.state_dir / "calls.jsonl", "a") as f:
        f.write("\n\n")
    status(cfg)
    assert "last call 21:28:26 UTC ok" in capsys.readouterr().out


def test_an_unreadable_last_line_is_said_so(cfg, capsys):
    log_calls(cfg, call())
    with open(cfg.state_dir / "calls.jsonl", "a") as f:
        f.write('{"time": "2026-10-29T21:3')
    assert status(cfg) == 0
    assert capsys.readouterr().out.endswith("· last call unreadable\n")


def test_a_missing_captures_folder_is_an_error(cfg, tmp_path, capsys):
    broken = ObserverConfig(captures_dir=tmp_path / "nope", model_dir=cfg.model_dir, state_dir=cfg.state_dir)
    assert status(broken) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"vitality-observer: cannot read {tmp_path / 'nope'}: No such file or directory\n"


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read anything")
def test_an_unreadable_calls_log_is_an_error(cfg, capsys):
    log_calls(cfg, call())
    (cfg.state_dir / "calls.jsonl").chmod(0)
    try:
        assert status(cfg) == 1
    finally:
        (cfg.state_dir / "calls.jsonl").chmod(0o644)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith(f"vitality-observer: cannot read {cfg.state_dir / 'calls.jsonl'}: ")
