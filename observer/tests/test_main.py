import asyncio
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from vitality_observer import main as observer_main
from vitality_observer.config import ObserverConfig
from vitality_observer.main import main, serve, watch_captures

from test_loop import FakeObserver, capture


@pytest.fixture
def cfg(tmp_path: Path) -> ObserverConfig:
    return ObserverConfig(
        captures_dir=tmp_path / "captures",
        model_dir=tmp_path / "model",
        state_dir=tmp_path / "state",
    )


def set_environment(monkeypatch, cfg: ObserverConfig) -> None:
    monkeypatch.setenv("OBSERVER_CAPTURES_DIR", str(cfg.captures_dir))
    monkeypatch.setenv("OBSERVER_MODEL_DIR", str(cfg.model_dir))
    monkeypatch.setenv("OBSERVER_STATE_DIR", str(cfg.state_dir))


async def until(condition, seconds: float = 5) -> None:
    async with asyncio.timeout(seconds):
        while not condition():
            await asyncio.sleep(0.02)


def wait_for_line(stream, text: str, seconds: float = 10) -> None:
    """Read `stream` until a line contains `text`; fail, rather than hang, if none does within `seconds`."""
    found = threading.Event()

    def read() -> None:
        for line in stream:
            if text in line:
                found.set()
                return

    threading.Thread(target=read, daemon=True).start()
    assert found.wait(seconds), f"no line containing {text!r} within {seconds} s"


async def stop(task: asyncio.Task) -> None:
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def test_dispatches_run_and_status(cfg, monkeypatch):
    set_environment(monkeypatch, cfg)
    called = []
    monkeypatch.setattr(observer_main, "run", lambda config: called.append(("run", config)) or 3)
    monkeypatch.setattr(observer_main, "status", lambda config: called.append(("status", config)) or 4)
    assert main(["run"]) == 3
    assert main(["status"]) == 4
    assert called == [("run", cfg), ("status", cfg)]


def test_no_command_or_an_unknown_one_exits_2(cfg, monkeypatch):
    set_environment(monkeypatch, cfg)
    for argv in ([], ["bogus"]):
        with pytest.raises(SystemExit) as exit_info:
            main(argv)
        assert exit_info.value.code == 2


def test_bad_setting_exits_1_naming_the_variable(monkeypatch, capsys):
    monkeypatch.setenv("OBSERVER_BATCH_SIZE", "many")
    monkeypatch.setattr(observer_main.asyncio, "run", lambda *a, **k: pytest.fail("must not run"))
    assert main(["run"]) == 1
    err = capsys.readouterr().err
    assert err.startswith("vitality-observer: ") and "OBSERVER_BATCH_SIZE" in err


def test_refuses_to_run_as_root(cfg, capsys, monkeypatch):
    set_environment(monkeypatch, cfg)
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(observer_main.asyncio, "run", lambda *a, **k: pytest.fail("must not run"))
    assert main(["run"]) == 1
    assert capsys.readouterr().err == (
        "vitality-observer: refusing to run as root; set VITALITY_UID to your own user\n")
    assert not cfg.state_dir.exists()


def test_unusable_state_dir_returns_1(tmp_path, cfg, capsys, monkeypatch):
    (tmp_path / "afile").write_text("")
    set_environment(monkeypatch, cfg)
    monkeypatch.setenv("OBSERVER_STATE_DIR", str(tmp_path / "afile" / "state"))
    assert main(["run"]) == 1
    assert capsys.readouterr().err.startswith("vitality-observer: ")


def test_run_creates_state_dir_and_serves_with_claude_code(cfg, monkeypatch):
    served = {}

    async def fake_serve(config, observer):
        served.update(config=config, observer=observer)

    set_environment(monkeypatch, cfg)
    monkeypatch.setattr(observer_main, "serve", fake_serve)
    assert main(["run"]) == 0
    assert cfg.state_dir.is_dir()
    assert served["config"] == cfg
    assert isinstance(served["observer"], observer_main.ClaudeCode)


def test_keyboard_interrupt_is_a_normal_stop(cfg, monkeypatch):
    def interrupted(coroutine):
        coroutine.close()
        raise KeyboardInterrupt

    set_environment(monkeypatch, cfg)
    monkeypatch.setattr(observer_main.asyncio, "run", interrupted)
    assert main(["run"]) == 0


@pytest.mark.skipif(os.geteuid() == 0, reason="the observer refuses to run as root")
def test_sigterm_stops_the_service_with_exit_code_0(cfg):
    cfg.captures_dir.mkdir()
    process = subprocess.Popen(
        [sys.executable, "-c", "import sys; from vitality_observer.main import main; sys.exit(main(sys.argv[1:]))",
         "run"],
        env={**os.environ,
             "OBSERVER_CAPTURES_DIR": str(cfg.captures_dir),
             "OBSERVER_MODEL_DIR": str(cfg.model_dir),
             "OBSERVER_STATE_DIR": str(cfg.state_dir)},
        stderr=subprocess.PIPE, text=True)
    assert process.stderr is not None
    try:
        wait_for_line(process.stderr, "observing")     # logged once the signal handlers are installed
        process.send_signal(signal.SIGTERM)
        assert process.wait(timeout=10) == 0
    finally:
        if process.poll() is None:
            process.kill()
        process.stderr.close()


async def test_serve_observes_a_capture_and_stops_on_cancel(cfg):
    cfg.captures_dir.mkdir()
    capture(cfg, "laptop/2026-10-25/10-00-00")
    observer = FakeObserver(cfg)
    task = asyncio.create_task(serve(cfg, observer))
    await until(lambda: observer.batches)
    assert observer.batches == [["laptop/2026-10-25/10-00-00"]]
    await stop(task)


async def test_serve_still_observes_by_rescan_when_captures_dir_is_missing(cfg):
    observer = FakeObserver(cfg)
    task = asyncio.create_task(serve(cfg, observer, rescan_seconds=0.05))
    await asyncio.sleep(0.3)
    assert not task.done()      # the watcher failing did not take the loop down
    capture(cfg, "laptop/2026-10-25/10-00-00")
    await until(lambda: observer.batches)
    assert not task.done()
    await stop(task)


async def test_serve_survives_the_watcher_raising(cfg, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("inotify limit reached")

    monkeypatch.setattr(observer_main, "awatch", broken)
    cfg.captures_dir.mkdir()
    observer = FakeObserver(cfg)
    task = asyncio.create_task(serve(cfg, observer, rescan_seconds=0.05))
    await asyncio.sleep(0.2)
    capture(cfg, "laptop/2026-10-25/10-00-00")
    await until(lambda: observer.batches)
    assert not task.done()
    await stop(task)


async def test_watcher_wakes_the_loop_for_a_new_directory(cfg):
    cfg.captures_dir.mkdir()
    woken = []
    task = asyncio.create_task(watch_captures(cfg.captures_dir, lambda: woken.append(1)))
    for n in range(100):    # keep adding until the watcher is up and has seen one
        (cfg.captures_dir / "laptop" / "2026-10-25" / f"10-00-{n:02d}").mkdir(parents=True, exist_ok=True)
        await asyncio.sleep(0.05)
        if woken:
            break
    assert woken
    await stop(task)


async def test_watcher_ignores_a_missing_directory(cfg, caplog):
    await watch_captures(cfg.captures_dir, lambda: pytest.fail("nothing to wake for"))
    assert "captures" in caplog.text


async def test_serve_wakes_on_watcher_events_before_the_rescan(cfg):
    """With a rescan far away, only the watcher can start the call for a capture made while idle."""
    cfg.captures_dir.mkdir()
    observer = FakeObserver(cfg)
    task = asyncio.create_task(serve(cfg, observer, rescan_seconds=60))
    await asyncio.sleep(0.5)    # idle: the first scan found nothing
    for n in range(100):
        name = f"laptop/2026-10-25/10-00-{n:02d}"
        capture(cfg, name)
        await asyncio.sleep(0.1)
        if observer.batches:
            break
    assert observer.batches
    await stop(task)
