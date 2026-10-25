import time
from datetime import UTC, datetime

import pytest
from vitality_server.cli import status as status_module
from vitality_server.cli.status import status

from stored import store_with


@pytest.fixture(autouse=True)
def utc_clock(monkeypatch):
    """Local time is UTC and 'now' is 2026-10-25 22:00, so output does not depend on the machine."""
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    monkeypatch.setattr(status_module, "_now", lambda: datetime(2026, 10, 25, 22, 0, tzinfo=UTC))
    yield
    monkeypatch.undo()
    time.tzset()


def config_for(tmp_path, data_dir):
    path = tmp_path / "server.toml"
    path.write_text(f'data_dir = "{data_dir}"\n')
    return path


def paths_block(config, data):
    return [
        f"config:   {config}",
        f"data:     {data}",
        f"captures: {data}/captures",
        f"index:    {data}/index.sqlite",
        "program:  /opt/vitality-server",
        "service:  /etc/systemd/system/vitality-server.service",
        "logs:     journalctl -u vitality-server",
    ]


def test_one_line_per_device_then_disk_then_paths(tmp_path, capsys):
    data = tmp_path / "data"
    store_with(data, {
        "laptop": [("2026-10-25T21:00:00Z", 2, datetime(2026, 10, 25, 21, 30, 37, tzinfo=UTC)),
                   ("2026-10-25T21:10:00Z", 1, datetime(2026, 10, 25, 21, 10, 0, tzinfo=UTC))],
        "desk": [("2026-10-20T09:00:00Z", 1, datetime(2026, 10, 20, 9, 5, 0, tzinfo=UTC))],
    })
    config = config_for(tmp_path, data)
    assert status(config) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "desk: 1 interactions · 1 shots · 0.0 kB · last received 2026-10-20 09:05"
    assert lines[1] == "laptop: 2 interactions · 3 shots · 0.1 kB · last received 21:30:37"
    assert lines[2].startswith("disk: ") and lines[2].endswith(" free")
    assert lines[3:] == paths_block(config, data)


def test_sizes_use_decimal_units(tmp_path, capsys, monkeypatch):
    data = tmp_path / "data"
    store_with(data, {"laptop": [("2026-10-25T21:00:00Z", 1, datetime(2026, 10, 25, 21, 0, tzinfo=UTC))]})
    monkeypatch.setattr(status_module.shutil, "disk_usage", lambda p: type("U", (), {"free": 19_700_000_000})())
    import sqlite3
    with sqlite3.connect(data / "index.sqlite") as db:
        db.execute("UPDATE shots SET bytes = 201000000")
    status(config_for(tmp_path, data))
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "laptop: 1 interactions · 1 shots · 201.0 MB · last received 21:00:00"
    assert out[1] == "disk: 19.7 GB free"


def test_last_received_is_shown_in_local_time(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("TZ", "America/Vancouver")  # UTC-7 in October
    time.tzset()
    data = tmp_path / "data"
    store_with(data, {"laptop": [("2026-10-25T21:00:00Z", 1, datetime(2026, 10, 25, 21, 30, 37, tzinfo=UTC))]})
    monkeypatch.setattr(status_module, "_now", lambda: datetime(2026, 10, 25, 22, 0, tzinfo=UTC))
    status(config_for(tmp_path, data))
    assert "last received 14:30:37" in capsys.readouterr().out


def test_empty_index(tmp_path, capsys):
    from vitality_server.storage import FileStore
    data = tmp_path / "data"
    FileStore(data).close()
    config = config_for(tmp_path, data)
    assert status(config) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "no interactions stored yet"
    assert lines[1].startswith("disk: ")
    assert lines[2:] == paths_block(config, data)


def test_missing_index_says_so_on_stderr_and_still_prints_paths(tmp_path, capsys):
    data = tmp_path / "data"
    data.mkdir()
    config = config_for(tmp_path, data)
    assert status(config) == 1
    captured = capsys.readouterr()
    assert captured.err.startswith(f"cannot read {data}/index.sqlite: ")
    assert captured.out.splitlines()[-7:] == paths_block(config, data)
    assert list(data.iterdir()) == []  # status creates nothing


def test_missing_data_folder_says_so_on_stderr_and_still_prints_paths(tmp_path, capsys):
    data = tmp_path / "nowhere"
    config = config_for(tmp_path, data)
    assert status(config) == 1
    captured = capsys.readouterr()
    assert "cannot read" in captured.err and str(data) in captured.err
    assert captured.out.splitlines() == paths_block(config, data)
    assert not data.exists()


def test_corrupt_index_says_so_on_stderr(tmp_path, capsys):
    data = tmp_path / "data"
    data.mkdir()
    (data / "index.sqlite").write_text("not a database")
    assert status(config_for(tmp_path, data)) == 1
    assert capsys.readouterr().err.startswith(f"cannot read {data}/index.sqlite: ")


def test_bad_config_goes_to_stderr_and_exits_1(tmp_path, capsys):
    path = tmp_path / "server.toml"
    path.write_text("bogus = 1\n")
    assert status(path) == 1
    captured = capsys.readouterr()
    assert captured.err.startswith("vitality-server: ") and "bogus" in captured.err
    assert captured.out == ""
