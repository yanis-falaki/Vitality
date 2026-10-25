import asyncio
import json

import httpx
import pytest
from PIL import Image

from http_forms import form_field, form_parts
from vitality_client.daemon.storage import Store, Writer
from vitality_client.daemon.upload import HttpUploader

T0 = 1761411600.449
stores: list[Store] = []


@pytest.fixture(autouse=True)
def close_stores():
    yield
    while stores:
        stores.pop().close()


def make_uploader(tmp_path, handler, **options):
    store = Store(tmp_path / "i.sqlite")
    stores.append(store)
    writer = Writer(tmp_path, store, 85, "UTC")
    writer.write(Image.new("RGB", (8, 8)), T0, T0, "activity")
    writer.end(T0)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://srv")
    return store, writer, HttpUploader("http://srv", store, tmp_path, client=client, **options)


def setup(tmp_path, handler):
    store, _, uploader = make_uploader(tmp_path, handler)
    return store, uploader


async def test_uploads_and_marks(tmp_path):
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(201, json={"ok": True, "stored": "new"})

    store, up = setup(tmp_path, handler)
    assert await up.upload_once() == "uploaded"
    assert seen[0].method == "POST" and seen[0].url.path == "/interactions"
    body = seen[0].content
    assert b'name="interaction"' in body and b'filename="0000.webp"' in body
    assert store.pending_uploads() == [] and up.status["state"] == "ok" and up.status["last_upload"]
    assert await up.upload_once() == "idle"


async def test_request_carries_interaction_json_and_the_shot_bytes(tmp_path):
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(201, json={"ok": True, "stored": "new"})

    store, up = setup(tmp_path, handler)
    name = store.pending_uploads()[0]
    await up.upload_once()
    on_disk = (tmp_path / "captures" / name / "interaction.json").read_text()
    assert json.loads(form_field(seen[0], "interaction")) == json.loads(on_disk)
    ((field, filename, content),) = [p for p in form_parts(seen[0]) if p[1] is not None]
    assert (field, filename) == ("shots", "0000.webp")
    assert content == (tmp_path / "captures" / name / "0000.webp").read_bytes()


async def test_existing_counts_as_uploaded(tmp_path):
    store, up = setup(tmp_path, lambda r: httpx.Response(200, json={"ok": True, "stored": "existing"}))
    assert await up.upload_once() == "uploaded" and store.pending_uploads() == []


@pytest.mark.parametrize("response", [httpx.Response(503), httpx.Response(507, json={"ok": False, "error": "disk full"})])
async def test_server_trouble_is_offline_and_keeps_pending(tmp_path, response):
    store, up = setup(tmp_path, lambda r: response)
    assert await up.upload_once() == "offline" and store.pending_count() == 1 and up.status["state"] == "offline"


async def test_connection_error_is_offline(tmp_path):
    def handler(req):
        raise httpx.ConnectError("no route")

    store, up = setup(tmp_path, handler)
    assert await up.upload_once() == "offline" and store.pending_count() == 1


async def test_timeout_is_offline(tmp_path):
    def handler(req):
        raise httpx.ReadTimeout("too slow")

    store, up = setup(tmp_path, handler)
    assert await up.upload_once() == "offline" and store.pending_count() == 1


async def test_forbidden_is_rejected(tmp_path):
    store, up = setup(tmp_path, lambda r: httpx.Response(403, json={"ok": False, "error": "device not allowed"}))
    assert await up.upload_once() == "rejected" and up.status["state"] == "rejected" and store.pending_count() == 1


@pytest.mark.parametrize("code", [409, 413, 422])
async def test_bad_interaction_is_skipped(tmp_path, code):
    store, up = setup(tmp_path, lambda r: httpx.Response(code, json={"ok": False, "error": "bad"}))
    assert await up.upload_once() == "failed" and store.pending_uploads() == []


async def test_bad_interaction_logs_name_and_reason(tmp_path, caplog):
    store, up = setup(tmp_path, lambda r: httpx.Response(409, json={"ok": False, "error": "name clash"}))
    name = store.pending_uploads()[0]
    await up.upload_once()
    errors = [r for r in caplog.records if r.levelname == "ERROR"]
    assert len(errors) == 1 and name in errors[0].getMessage() and "name clash" in errors[0].getMessage()


async def test_missing_local_file_is_skipped(tmp_path):
    store, up = setup(tmp_path, lambda r: httpx.Response(201, json={"ok": True, "stored": "new"}))
    next(tmp_path.glob("captures/*/*/0000.webp")).unlink()
    assert await up.upload_once() == "failed"
    assert store.pending_uploads() == []


async def test_repeated_offline_logs_one_state_change(tmp_path, caplog):
    caplog.set_level("INFO")
    store, up = setup(tmp_path, lambda r: httpx.Response(503))
    for _ in range(3):
        await up.upload_once()
    assert len([r for r in caplog.records if "offline" in r.getMessage()]) == 1


async def test_recovery_is_logged_once(tmp_path, caplog):
    caplog.set_level("INFO")
    responses = [httpx.Response(503), httpx.Response(201, json={"ok": True, "stored": "new"})]
    store, up = setup(tmp_path, lambda r: responses.pop(0))
    assert await up.upload_once() == "offline"
    assert await up.upload_once() == "uploaded"
    assert up.status["state"] == "ok"
    assert "offline -> ok" in caplog.text


async def test_status_pending_follows_the_index(tmp_path):
    store, writer, up = make_uploader(tmp_path, lambda r: httpx.Response(503))
    assert up.status == {"state": "ok", "pending": 1, "last_upload": None}
    writer.write(Image.new("RGB", (8, 8)), T0 + 100, T0 + 100, "activity")
    writer.end(T0 + 100)
    assert up.status["pending"] == 2


async def test_uploads_oldest_first_and_keeps_going(tmp_path):
    seen = []

    def handler(req):
        seen.append(json.loads(form_field(req, "interaction"))["name"])
        return httpx.Response(201, json={"ok": True, "stored": "new"})

    store, writer, up = make_uploader(tmp_path, handler)
    first = store.pending_uploads()[0]
    writer.write(Image.new("RGB", (8, 8)), T0 + 100, T0 + 100, "activity")
    second = writer.end(T0 + 100)
    task = asyncio.create_task(up.run())
    for _ in range(200):
        if len(seen) == 2:
            break
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert seen == [first, second]


async def test_wake_triggers_prompt_upload(tmp_path):
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(201, json={"ok": True, "stored": "new"})

    store, writer, up = make_uploader(tmp_path, handler, idle_wait=3600)
    task = asyncio.create_task(up.run())
    try:
        async with asyncio.timeout(1):
            while not seen:
                await asyncio.sleep(0.01)
        await asyncio.sleep(0.1)  # the loop is now idle, waiting out its hour
        writer.write(Image.new("RGB", (8, 8)), T0 + 100, T0 + 100, "activity")
        writer.end(T0 + 100)
        await asyncio.sleep(0.1)
        assert len(seen) == 1  # nothing happens until woken
        up.wake()
        async with asyncio.timeout(1):
            while len(seen) < 2:
                await asyncio.sleep(0.01)
        assert store.pending_uploads() == []
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_offline_backs_off_then_resets_after_success(tmp_path):
    attempts = []
    outcomes = [503, 503, 503, 201]

    def handler(req):
        attempts.append(asyncio.get_running_loop().time())
        code = outcomes.pop(0)
        return httpx.Response(code, json={"ok": True, "stored": "new"})

    store, writer, up = make_uploader(tmp_path, handler, retry_initial=0.05, retry_max=0.1,
                                      idle_wait=3600)
    task = asyncio.create_task(up.run())
    try:
        async with asyncio.timeout(2):
            while len(attempts) < 4:
                await asyncio.sleep(0.01)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    gaps = [b - a for a, b in zip(attempts, attempts[1:])]
    assert 0.04 <= gaps[0] < 0.09 and gaps[1] >= 0.09 and gaps[2] >= 0.09  # 0.05, 0.1, capped at 0.1


async def test_rejected_waits_before_retrying(tmp_path):
    attempts = []

    def handler(req):
        attempts.append(req)
        return httpx.Response(403, json={"ok": False, "error": "no"})

    store, writer, up = make_uploader(tmp_path, handler, rejected_wait=3600, retry_initial=0.01)
    task = asyncio.create_task(up.run())
    try:
        await asyncio.sleep(0.2)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert len(attempts) == 1 and up.status["state"] == "rejected"


async def test_run_closes_its_own_client_but_not_an_injected_one(tmp_path):
    store, _, up = make_uploader(tmp_path, lambda r: httpx.Response(503), idle_wait=3600)
    injected = up._client
    assert injected is not None
    task = asyncio.create_task(up.run())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not injected.is_closed

    own = HttpUploader("http://127.0.0.1:9", store, tmp_path, retry_initial=3600)
    task = asyncio.create_task(own.run())
    await asyncio.sleep(0.05)
    created = own._client
    assert created is not None and not created.is_closed
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert created.is_closed
