import asyncio
import json
import os
import signal
import sqlite3
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image

from vitality_client.shared.config import Config, Paths
from vitality_client.shared.channel import UploadStatus, send_command
from vitality_client.shared.state import StateFile
from vitality_client.daemon.daemon import CaptureDaemon
from vitality_client.daemon.storage import Store
from vitality_client.daemon.storage import Writer

img = Image.new("RGB", (64, 40), "white")


class FakeFrames:
    def __init__(self, frame):
        self.frame = frame
        self.available = True
        self.closed = False

    async def start(self, on_restore_token):
        pass

    def latest_frame(self):
        return self.frame

    async def close(self):
        self.closed = True


class NoopWatcher:
    available = False

    async def run(self, on_activity):
        await asyncio.Event().wait()


class FakeClock:
    def __init__(self, t=0.0):
        self.t = t

    def __call__(self):
        return self.t


def sqlite_rows(db):
    """Every shot with its interaction's `end`, oldest first; `path` is relative to the data dir."""
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(
            "SELECT s.*, 'captures/' || s.interaction || '/' || s.file AS path, i.end "
            "FROM shots s JOIN interactions i ON i.name = s.interaction "
            "ORDER BY s.ts, s.rowid")]
    finally:
        conn.close()


def interaction_files(data):
    """{interaction name: sorted file names} for every folder under captures/."""
    return {d.relative_to(data / "captures").as_posix(): sorted(p.name for p in d.iterdir())
            for d in sorted((data / "captures").glob("*/*"))}


@pytest.fixture
async def daemon_env(tmp_path):
    made = []

    def make(frame=img, reuse=None, config=None, clock=None, uploader=None):
        data = reuse if reuse is not None else tmp_path / "data"
        paths = Paths(tmp_path / "config/client.toml", data,
                      tmp_path / "state/state.json", tmp_path / "run/vitality/client.sock")
        clock = clock or FakeClock()
        d = CaptureDaemon(config or Config(), paths, FakeFrames(frame), NoopWatcher(), clock=clock,
                          uploader=uploader)
        made.append(d)
        return d, clock, data

    yield make
    for d in made:
        await d.close()


async def wait_for_socket(d):
    for _ in range(200):
        if d._paths.socket_path.exists():
            return
        await asyncio.sleep(0.01)


async def test_activity_burst_writes_one_interaction(daemon_env):
    d, clock, data = daemon_env()
    d.handle_activity(100.0); clock.t = 102.0; await d.tick_once()
    folder = next((data / "captures").glob("*/*"))
    assert sorted(p.name for p in folder.iterdir()) == ["0000.webp", "0002.webp", "interaction.json"]
    rows = sqlite_rows(data / "index.sqlite")
    assert [r["trigger"] for r in rows] == ["activity", "activity"]
    assert rows[0]["interaction"] == rows[1]["interaction"] and rows[0]["end"] is not None
    assert all((data / r["path"]).exists() for r in rows)


async def test_no_frame_drops_snapshot(daemon_env, caplog):                  # Review Focus 1
    caplog.set_level("INFO")
    d, clock, data = daemon_env(frame=None)
    d.handle_activity(100.0); await d.drain()
    assert sqlite_rows(data / "index.sqlite") == []
    assert "snapshot dropped: no frame available" in caplog.text


async def test_frame_source_error_drops_snapshot(daemon_env, caplog):
    d, clock, data = daemon_env()
    with patch.object(FakeFrames, "latest_frame", side_effect=ValueError("bad stride")):
        d.handle_activity(100.0)  # must not raise into the activity watcher
    await d.drain()
    assert "bad stride" in caplog.text and sqlite_rows(data / "index.sqlite") == []


async def test_frame_is_read_at_request_time(daemon_env):
    d, clock, data = daemon_env()
    first = Image.new("RGB", (10, 10), "red")
    d._frames.frame = first
    d.handle_activity(100.0)
    d._frames.frame = None  # gone by the time the write runs; the request already holds `first`
    await d.drain()
    assert [r["width"] for r in sqlite_rows(data / "index.sqlite")] == [10]


async def test_write_failure_is_logged_and_daemon_continues(daemon_env, caplog):   # Review Focus 5
    d, clock, data = daemon_env()
    with patch.object(Writer, "write", side_effect=OSError("disk full")):
        d.handle_activity(100.0); await d.drain()
    assert "disk full" in caplog.text
    clock.t = 200.0; d.handle_activity(200.0); await d.drain()
    # the overdue settle closes the first interaction (its final shot), then a new one starts
    assert interaction_files(data) == {
        "1970-01-01/00-01-40": ["0100.webp", "interaction.json"],
        "1970-01-01/00-03-20": ["0000.webp"],
    }


async def test_end_failure_is_logged_and_daemon_continues(daemon_env, caplog):
    d, clock, data = daemon_env()
    with patch.object(Writer, "end", side_effect=OSError("disk full")):
        d.handle_activity(100.0); clock.t = 102.0; await d.tick_once()
    assert "disk full" in caplog.text
    d.handle_activity(200.0); clock.t = 202.0; await d.tick_once()
    assert interaction_files(data)["1970-01-01/00-03-20"] == ["0000.webp", "0002.webp",
                                                              "interaction.json"]


async def test_writes_are_serialized_in_request_order(daemon_env):
    d, clock, data = daemon_env()
    active = 0
    peak = 0
    order = []
    real_write, real_end = Writer.write, Writer.end

    def slow_write(self, image, start, ts, trigger):
        nonlocal active, peak
        active += 1; peak = max(peak, active)
        time.sleep(0.02)
        try:
            return real_write(self, image, start, ts, trigger)
        finally:
            order.append("write")
            active -= 1

    def recording_end(self, start):
        order.append("end")
        return real_end(self, start)

    with patch.object(Writer, "write", slow_write), patch.object(Writer, "end", recording_end):
        d.handle_activity(100.0)             # the first shot
        clock.t = 101.0
        await d.handle({"cmd": "pause", "seconds": None})   # the final shot, then the end
        await d.drain()
    assert peak == 1 and order == ["write", "write", "end"]
    assert [r["file"] for r in sqlite_rows(data / "index.sqlite")] == ["0000.webp", "0001.webp"]
    assert interaction_files(data) == {"1970-01-01/00-01-40": ["0000.webp", "0001.webp",
                                                               "interaction.json"]}


async def test_pause_persists_and_survives_restart(daemon_env):
    d, clock, data = daemon_env()
    assert (await d.handle({"cmd": "pause", "seconds": None}))["ok"]
    d2, _, _ = daemon_env(reuse=data)
    assert d2.status()["state"] == "paused"


async def test_timed_pause_expiry_is_persisted(daemon_env):
    d, clock, data = daemon_env()
    clock.t = 100.0
    resp = await d.handle({"cmd": "pause", "seconds": 5})
    assert resp["ok"] and resp["state"] == "paused" and resp["paused_until"] == "1970-01-01T00:01:45.000Z"
    clock.t = 106.0; await d.tick_once()
    assert d.status()["state"] == "idle"
    d2, _, _ = daemon_env(reuse=data)
    assert d2.status()["state"] == "idle"


async def test_resume_persists_and_keeps_restore_token(daemon_env, tmp_path):
    d, clock, data = daemon_env()
    d.save_restore_token("tok")
    await d.handle({"cmd": "pause", "seconds": None})
    assert (await d.handle({"cmd": "resume"}))["state"] == "idle"
    state = StateFile(tmp_path / "state/state.json").load()
    assert (state.paused, state.restore_token) == (False, "tok")


@pytest.mark.parametrize("request_,error", [
    ({"cmd": "explode"}, "unknown command: explode"),
    ({"cmd": "pause", "seconds": "soon"}, "invalid pause duration"),
    ({"cmd": "pause", "seconds": -1}, "invalid pause duration"),
])
async def test_bad_requests(daemon_env, request_, error):
    d, _, _ = daemon_env()
    assert await d.handle(request_) == {"ok": False, "error": error}
    assert d.status()["state"] == "idle"


async def test_status_fields(daemon_env):
    d, _, _ = daemon_env()
    s = d.status()
    assert set(s) >= {"ok", "state", "paused_until", "captures_today", "last_capture", "disk_bytes",
                      "disk_warning", "activity", "capture", "paths", "upload"}
    assert set(s["paths"]) == {"config", "data", "state"}


async def test_status_values(daemon_env):
    now = time.time()
    d, clock, data = daemon_env(clock=FakeClock(now), config=Config(disk_warning_gb=0))
    assert d.status()["last_capture"] is None and d.status()["captures_today"] == 0
    d.handle_activity(now); await d.drain()
    s = d.status()
    assert s["ok"] is True and s["state"] == "active" and s["paused_until"] is None
    assert s["captures_today"] == 1 and s["last_capture"] == sqlite_rows(data / "index.sqlite")[0]["ts"]
    assert s["disk_bytes"] > 0 and s["disk_warning"] is True
    assert (s["activity"], s["capture"]) == ("unavailable", "available")
    assert s["paths"]["data"] == str(data)


async def test_run_serves_control_ticks_and_stops_on_sigterm(daemon_env):
    d, clock, data = daemon_env(clock=time.time, config=Config(settle_seconds=0.05))
    task = asyncio.create_task(d.run())
    sock = d._paths.socket_path
    await wait_for_socket(d)
    reply = await send_command(sock, {"cmd": "status"})
    assert reply["ok"] and "state" in reply and reply["state"] == "idle"
    d.handle_activity(time.time())
    for _ in range(200):  # the tick loop wakes for the new deadline and closes the interaction
        if list(data.glob("captures/*/*/interaction.json")):
            break
        await asyncio.sleep(0.01)
    rows = sqlite_rows(data / "index.sqlite")
    assert len(rows) == 2 and rows[0]["file"] == "0000.webp" and rows[1]["end"] is not None
    os.kill(os.getpid(), signal.SIGTERM)
    await asyncio.wait_for(task, 5)
    assert not sock.exists() and d._frames.closed


async def test_run_refuses_second_instance_without_touching_files(daemon_env):
    d, _, data = daemon_env()
    task = asyncio.create_task(d.run())
    await wait_for_socket(d)
    stray = data / "captures/1970-01-01/00-00-00/0000.webp.tmp"
    stray.parent.mkdir(parents=True); stray.write_bytes(b"in flight")
    d2, _, _ = daemon_env(reuse=data)
    with pytest.raises(RuntimeError, match="already running"):
        await d2.run()
    assert stray.exists()  # the live daemon's in-flight temp file was not cleaned up
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def test_disk_usage_skips_files_that_vanish_during_scan(tmp_path):
    w = Writer(tmp_path, Store(tmp_path / "i.sqlite"), 85, "UTC")
    row = w.write(img, 0.0, 0.0, "activity")
    (tmp_path / "captures/1970-01-01/00-00-00/G.webp.tmp").write_bytes(b"x")
    real_stat = Path.stat

    def racing_stat(self, *a, **kw):
        # A concurrent write renames the temp file between the directory listing and stat().
        if self.name == "G.webp.tmp":
            raise FileNotFoundError(self)
        return real_stat(self, *a, **kw)

    with patch.object(Path, "stat", racing_stat):
        assert w.disk_usage_bytes() == row.bytes


def disk_warnings(caplog):
    return [r for r in caplog.records if r.levelname == "WARNING" and "disk_warning_gb" in r.getMessage()]


async def test_disk_warning_logged_once_when_crossed(daemon_env, caplog):
    d, clock, data = daemon_env(config=Config(disk_warning_gb=1e-9))  # 1 byte
    for t in (100.0, 200.0):  # two interactions: a first and a final shot each
        d.handle_activity(t); clock.t = t + 3; await d.tick_once()
    assert len(sqlite_rows(data / "index.sqlite")) == 4
    assert len(disk_warnings(caplog)) == 1 and d.status()["disk_warning"] is True


async def test_disk_usage_back_under_threshold_logs_info_once(daemon_env, caplog):
    caplog.set_level("INFO")
    d, _, _ = daemon_env(config=Config(disk_warning_gb=1e-6))  # 1000 bytes
    d._record_disk_usage(5000); d._record_disk_usage(6000)
    d._record_disk_usage(10); d._record_disk_usage(20)
    assert len(disk_warnings(caplog)) == 1
    assert sum("back under disk_warning_gb" in r.getMessage() for r in caplog.records) == 1


async def test_status_disk_bytes_is_cached_not_rescanned(daemon_env):
    d, clock, data = daemon_env()
    with patch.object(Writer, "disk_usage_bytes", side_effect=AssertionError("rescanned")):
        for t in (100.0, 200.0):  # two interactions: a first and a final shot each
            d.handle_activity(t); clock.t = t + 3; await d.tick_once()
        rows = sqlite_rows(data / "index.sqlite")
        assert len(rows) == 4 and d.status()["disk_bytes"] == sum(r["bytes"] for r in rows)


async def test_run_scans_existing_disk_usage_once_at_startup(daemon_env, caplog):
    d, clock, data = daemon_env()
    d.handle_activity(100.0); await d.drain()
    await d.close()
    d2, _, _ = daemon_env(reuse=data, config=Config(disk_warning_gb=1e-9))
    task = asyncio.create_task(d2.run())
    for _ in range(200):
        if d2.status()["disk_bytes"]:
            break
        await asyncio.sleep(0.01)
    # the scan runs after the interrupted interaction is closed, so it counts interaction.json
    existing = sum(p.stat().st_size for p in (data / "captures").rglob("*") if p.is_file())
    assert interaction_files(data) == {"1970-01-01/00-01-40": ["0000.webp", "interaction.json"]}
    assert d2.status()["disk_bytes"] == existing and len(disk_warnings(caplog)) == 1
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_capture_command_saves_one_screenshot_and_returns_its_path(daemon_env):
    d, _, data = daemon_env()
    reply = await d.handle({"cmd": "capture"})
    assert reply["ok"] is True
    rows = sqlite_rows(data / "index.sqlite")
    assert [(r["file"], r["trigger"]) for r in rows] == [("0000.webp", "manual")]
    assert reply["path"] == str((data / rows[0]["path"]).resolve())
    assert (data / rows[0]["path"]).exists()


async def test_capture_command_joins_open_interaction(daemon_env):
    d, clock, data = daemon_env()
    d.handle_activity(100.0); clock.t = 101.0
    reply = await d.handle({"cmd": "capture"})
    assert reply["ok"] and reply["path"].endswith("/0001.webp")
    assert d.status()["state"] == "active"


async def test_capture_command_while_idle_is_one_shot_interaction(daemon_env):
    d, clock, data = daemon_env(); clock.t = 50.0
    await d.handle({"cmd": "capture"}); await d.drain()
    (meta,) = (data / "captures").glob("*/*/interaction.json")
    assert json.loads(meta.read_text())["shots"][0]["trigger"] == "manual"


async def test_capture_command_without_a_frame_is_an_error(daemon_env):
    d, _, data = daemon_env(frame=None)
    reply = await d.handle({"cmd": "capture"})
    assert reply == {"ok": False, "error": "no screen frame available yet"}
    assert sqlite_rows(data / "index.sqlite") == []


async def test_capture_command_works_while_paused(daemon_env):
    # An explicit capture is the user's own request, so pause doesn't block it.
    d, _, data = daemon_env()
    await d.handle({"cmd": "pause", "seconds": None})
    reply = await d.handle({"cmd": "capture"})
    assert reply["ok"] is True and d.status()["state"] == "paused"
    await d.drain()
    assert interaction_files(data) == {"1970-01-01/00-00-00": ["0000.webp", "interaction.json"]}


async def test_capture_command_reports_a_failed_write(daemon_env):
    d, _, data = daemon_env()
    with patch.object(Writer, "write", side_effect=OSError("disk full")):
        reply = await d.handle({"cmd": "capture"})
    assert reply == {"ok": False, "error": "capture failed: disk full"}


class FakeUploader:
    status: UploadStatus = {"state": "ok", "pending": 3, "last_upload": "2026-10-25T17:00:00.000Z"}

    def __init__(self):
        self.woken = []

    async def run(self):
        await asyncio.Event().wait()

    def wake(self):
        self.woken.append(True)


async def test_interaction_end_wakes_uploader(daemon_env):
    woken = []

    class WakeRecorder(FakeUploader):
        def wake(self):
            woken.append(True)

    d, clock, _ = daemon_env(uploader=lambda index: WakeRecorder())
    d.handle_activity(100.0); clock.t = 102.0; await d.tick_once()
    assert woken == [True]


async def test_end_without_shots_does_not_wake_uploader(daemon_env):
    uploader = FakeUploader()
    d, clock, _ = daemon_env(uploader=lambda index: uploader)
    with patch.object(FakeFrames, "latest_frame", return_value=None):
        d.handle_activity(100.0); clock.t = 102.0; await d.tick_once()
    assert uploader.woken == []


async def test_uploader_is_built_from_the_index(daemon_env):
    given = []

    def factory(index):
        given.append(index)
        return FakeUploader()

    d, _, _ = daemon_env(uploader=factory)
    assert given == [d._store]


async def test_status_upload_disabled_without_uploader(daemon_env):
    d, _, _ = daemon_env()
    assert d.status()["upload"] == {"state": "disabled", "pending": 0, "last_upload": None}


async def test_status_upload_disabled_counts_pending_interactions(daemon_env):
    d, clock, _ = daemon_env()
    d.handle_activity(100.0); clock.t = 102.0; await d.tick_once()
    assert d.status()["upload"] == {"state": "disabled", "pending": 1, "last_upload": None}


async def test_status_upload_comes_from_the_uploader(daemon_env):
    d, _, _ = daemon_env(uploader=lambda index: FakeUploader())
    assert d.status()["upload"] == FakeUploader.status


async def test_run_starts_the_uploader(daemon_env):
    started = asyncio.Event()

    class StartRecorder(FakeUploader):
        async def run(self):
            started.set()
            await asyncio.Event().wait()

    d, _, _ = daemon_env(uploader=lambda index: StartRecorder())
    task = asyncio.create_task(d.run())
    await asyncio.wait_for(started.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_uploader_crash_is_logged_and_daemon_keeps_running(daemon_env, caplog):
    class Crashing(FakeUploader):
        async def run(self):
            raise RuntimeError("upload loop exploded")

    d, _, _ = daemon_env(uploader=lambda index: Crashing())
    task = asyncio.create_task(d.run())
    await wait_for_socket(d)
    for _ in range(200):
        if "upload loop exploded" in caplog.text:
            break
        await asyncio.sleep(0.01)
    assert "upload loop exploded" in caplog.text
    reply = await send_command(d._paths.socket_path, {"cmd": "status"})
    assert reply["ok"] and not task.done()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_run_closes_interrupted_interaction_at_startup(daemon_env, caplog):
    caplog.set_level("INFO")
    d, _, data = daemon_env()
    d.handle_activity(100.0); await d.drain()  # one shot; no settle tick, so no end
    await d.close()
    assert list(data.glob("captures/*/*/interaction.json")) == []
    d2, _, _ = daemon_env(reuse=data)
    task = asyncio.create_task(d2.run())
    await wait_for_socket(d2)
    assert (await send_command(d2._paths.socket_path, {"cmd": "status"}))["ok"]
    assert (data / "captures/1970-01-01/00-01-40/interaction.json").exists()
    assert "closed 1 interrupted interaction(s)" in caplog.text
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
