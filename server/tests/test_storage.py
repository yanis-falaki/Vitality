import threading

import pytest
from vitality_protocol.messages import Interaction
from vitality_server.shared.storage import ConflictingUpload, FileStore, InvalidUpload

WEBP = b"RIFF\x10\x00\x00\x00WEBPVP8 " + b"\x00" * 8
META = Interaction.model_validate({
    "name": "2026-10-25/17-00-00", "source": "screen",
    "start": "2026-10-25T17:00:00.449Z", "end": "2026-10-25T17:00:04.120Z",
    "local_start": "2026-10-25T10:00:00.449-07:00", "tz": "America/Vancouver",
    "shots": [{"file": "0000.webp", "ts": "2026-10-25T17:00:00.449Z", "trigger": "activity",
               "width": 8, "height": 8, "bytes": len(WEBP)}]})
FILES = {"0000.webp": WEBP}


def test_put_stores_the_folder_and_nothing_else(tmp_path):
    s = FileStore(tmp_path)
    assert s.put("laptop", META, FILES) == "new"
    folder = tmp_path / "captures/laptop/2026-10-25/17-00-00"
    assert (folder / "0000.webp").read_bytes() == WEBP
    assert Interaction.model_validate_json((folder / "interaction.json").read_text()) == META
    assert not any((tmp_path / "tmp").iterdir())
    assert sorted(p.name for p in tmp_path.iterdir()) == ["captures", "tmp"]


def test_identical_repeat_is_existing(tmp_path):
    s = FileStore(tmp_path); s.put("laptop", META, FILES)
    assert s.put("laptop", META, FILES) == "existing"


def test_same_interaction_different_json_formatting_is_existing(tmp_path):  # Review Focus 3
    s = FileStore(tmp_path); s.put("laptop", META, FILES)
    folder = tmp_path / "captures/laptop/2026-10-25/17-00-00"
    (folder / "interaction.json").write_text(META.model_dump_json(indent=4).replace("Z\"", "+00:00\""))
    assert s.put("laptop", META, FILES) == "existing"


def test_different_content_same_name_conflicts(tmp_path):
    s = FileStore(tmp_path); s.put("laptop", META, FILES)
    with pytest.raises(ConflictingUpload):
        s.put("laptop", META.model_copy(update={"tz": "UTC"}), FILES)


def test_suffixed_name_is_accepted(tmp_path):                               # Review Focus 2
    meta = META.model_copy(update={"name": "2026-10-25/17-00-00-2"})
    assert FileStore(tmp_path).put("laptop", meta, FILES) == "new"


@pytest.mark.parametrize("meta,files,reason", [
    (META.model_copy(update={"name": "2026-10-25/17-00-01"}), FILES, "name"),
    (META, {}, "files"),
    (META, {"0000.webp": WEBP, "0001.webp": WEBP}, "files"),
    (META, {"0000.webp": WEBP + b"x"}, "size"),
    (META, {"0000.webp": b"\x89PNG" + b"\x00" * 20}, "WebP"),   # same length as WEBP
])
def test_invalid_uploads_rejected(tmp_path, meta, files, reason):
    with pytest.raises(InvalidUpload, match=reason):
        FileStore(tmp_path).put("laptop", meta, files)
    assert not (tmp_path / "captures").exists()


def test_recover_removes_unfinished_uploads_and_an_old_index(tmp_path):
    s = FileStore(tmp_path); s.put("laptop", META, FILES)
    (tmp_path / "tmp/abc").mkdir(); (tmp_path / "tmp/abc/0000.webp").write_bytes(WEBP)
    (tmp_path / "tmp/def").mkdir()
    for name in ("index.sqlite", "index.sqlite-wal", "index.sqlite-shm"):  # left by older servers
        (tmp_path / name).write_bytes(b"x")
    assert s.recover() == 2
    assert sorted(p.name for p in tmp_path.iterdir()) == ["captures", "tmp"]
    assert not any((tmp_path / "tmp").iterdir())
    assert s.put("laptop", META, FILES) == "existing"


def test_same_name_from_two_clients_is_stored_separately(tmp_path):
    s = FileStore(tmp_path)
    assert s.put("laptop", META, FILES) == "new"
    assert s.put("desktop", META, FILES) == "new"
    assert (tmp_path / "captures/desktop/2026-10-25/17-00-00/interaction.json").exists()


@pytest.mark.parametrize("client", ["", "..", "../x", "a/b", "a..b", "/abs", "a\\b", "a\x00b"])
def test_unsafe_client_is_rejected(tmp_path, client):
    with pytest.raises(InvalidUpload, match="client"):
        FileStore(tmp_path).put(client, META, FILES)
    assert not (tmp_path / "captures").exists()


def test_duplicate_shot_files_are_rejected(tmp_path):
    shot = META.shots[0]
    meta = META.model_copy(update={"shots": [shot, shot]})
    with pytest.raises(InvalidUpload, match="files"):
        FileStore(tmp_path).put("laptop", meta, FILES)


def test_file_name_other_than_a_shot_name_is_rejected(tmp_path):
    with pytest.raises(InvalidUpload, match="files"):
        FileStore(tmp_path).put("laptop", META, {"../0000.webp": WEBP})


def test_failed_write_leaves_nothing_behind(tmp_path, monkeypatch):
    import os
    s = FileStore(tmp_path)
    def boom(*a, **k):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(os, "rename", boom)
    with pytest.raises(OSError):
        s.put("laptop", META, FILES)
    assert not any((tmp_path / "tmp").iterdir())
    assert not (tmp_path / "captures/laptop/2026-10-25/17-00-00").exists()


def test_disk_free_bytes_is_positive(tmp_path):
    assert FileStore(tmp_path).disk_free_bytes() > 0


def test_concurrent_identical_uploads_store_once(tmp_path):
    s = FileStore(tmp_path)
    results: list[str] = []
    errors: list[BaseException] = []
    barrier = threading.Barrier(8)

    def upload():
        barrier.wait()
        try:
            results.append(s.put("laptop", META, FILES))
        except BaseException as e:  # noqa: BLE001 - the test reports whatever happened
            errors.append(e)

    threads = [threading.Thread(target=upload) for _ in range(8)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert not errors
    assert sorted(results) == ["existing"] * 7 + ["new"]


# --- passive shots and screen names (desktop client) ---

TWO_SCREENS = Interaction.model_validate({
    **META.model_dump(mode="json"),
    "shots": [{"file": "0000.webp", "ts": "2026-10-25T17:00:00.449Z", "trigger": "passive",
               "width": 8, "height": 8, "bytes": len(WEBP), "screen_name": "Main"},
              {"file": "0000-2.webp", "ts": "2026-10-25T17:00:00.449Z", "trigger": "passive",
               "width": 8, "height": 8, "bytes": len(WEBP), "screen_name": "Side"}]})


def test_two_screen_passive_interaction_is_stored(tmp_path):
    s = FileStore(tmp_path)
    assert s.put("desktop", TWO_SCREENS, {"0000.webp": WEBP, "0000-2.webp": WEBP}) == "new"
    folder = tmp_path / "captures/desktop/2026-10-25/17-00-00"
    assert Interaction.model_validate_json((folder / "interaction.json").read_text()) == TWO_SCREENS


def test_old_style_interaction_json_has_no_new_fields(tmp_path):
    FileStore(tmp_path).put("laptop", META, FILES)
    import json
    written = json.loads((tmp_path / "captures/laptop/2026-10-25/17-00-00/interaction.json").read_text())
    assert written == META.model_dump(mode="json", exclude_none=True)
    assert "screen_name" not in written["shots"][0]
