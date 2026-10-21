"""Screen capture via xdg-desktop-portal ScreenCast -> PipeWire -> GStreamer.

One portal session covers every monitor; each monitor is a PipeWire stream with its
own GStreamer pipeline. The appsink callback runs on a GStreamer thread and only
stores the newest frame under a lock; everything else runs on the asyncio loop.

Never logged: image contents and the restore token value.
"""

import asyncio
import logging
import os
import secrets
import threading
import time
from collections.abc import Callable

import gi

gi.require_version("Gst", "1.0")
gi.require_version("GstVideo", "1.0")
from dbus_fast import BusType, Message, MessageType, Variant  # noqa: E402
from dbus_fast.aio import MessageBus  # noqa: E402
from gi.repository import Gst, GstVideo  # noqa: E402
from PIL import Image  # noqa: E402

from vitality_client.frames.base import composite, layout_frames  # noqa: E402

log = logging.getLogger(__name__)

PORTAL_BUS_NAME = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
SCREENCAST_IFACE = "org.freedesktop.portal.ScreenCast"
REQUEST_IFACE = "org.freedesktop.portal.Request"
SESSION_IFACE = "org.freedesktop.portal.Session"

SOURCE_TYPE_MONITOR = 1
CURSOR_MODE_EMBEDDED = 2
PERSIST_MODE_UNTIL_REVOKED = 2

RESPONSE_CANCELLED = 1
_RESPONSE_MEANING = {RESPONSE_CANCELLED: "cancelled by the user", 2: "ended by the portal"}
_WATCH_INTERVAL = 0.5
_STABLE_SESSION_SECONDS = 60  # a session that ran this long resets the retry backoff
_CLOSE_TIMEOUT = 2.0


def request_path(unique_name: str, handle_token: str) -> str:
    """Object path of the Request a portal call with this handle_token will create."""
    sender = unique_name.lstrip(":").replace(".", "_")
    return f"{PORTAL_PATH}/request/{sender}/{handle_token}"


def pipeline_description(fd: int, node_id: int, max_fps: int) -> str:
    return (
        f"pipewiresrc fd={fd} path={node_id} always-copy=true keepalive-time=1000 ! "
        f"videorate max-rate={max_fps} ! videoconvert ! video/x-raw,format=RGB ! "
        "appsink name=sink max-buffers=1 drop=true sync=false emit-signals=true"
    )


def backoff_delay(failures: int) -> float:
    """Seconds to wait after the n-th consecutive failure: 2, 4, 8, ... capped at 60."""
    return min(2 ** failures, 60)


def rgb_image_from_bytes(data: bytes, width: int, height: int, stride: int) -> Image.Image:
    """Packed RGB rows of `stride` bytes (>= width * 3; extra bytes are padding) -> image."""
    return Image.frombytes("RGB", (width, height), data, "raw", "RGB", stride)


class PortalError(Exception):
    pass


class PortalResponseError(PortalError):
    def __init__(self, method: str, response: int) -> None:
        meaning = _RESPONSE_MEANING.get(response, f"response {response}")
        super().__init__(f"portal {method} {meaning}")
        self.response = response


def should_retry(exc: Exception) -> bool:
    """False when the user cancelled the permission dialog; every other failure retries."""
    return not (isinstance(exc, PortalResponseError) and exc.response == RESPONSE_CANCELLED)


class PortalFrameSource:
    def __init__(
        self,
        restore_token: str | None,
        on_restore_token: Callable[[str], None],
        max_fps: int = 5,
    ) -> None:
        self._restore_token = restore_token
        self._on_restore_token = on_restore_token
        self._max_fps = max_fps
        self.available = False

        self._task: asyncio.Task | None = None
        self._first_attempt = asyncio.Event()

        self._bus: MessageBus | None = None
        self._session: str | None = None
        self._session_closed = False
        self._fd: int | None = None
        self._pipelines: list[Gst.Pipeline] = []

        # Shared with GStreamer threads.
        self._lock = threading.Lock()
        self._positions: list[tuple[int, int]] = []  # logical, from the portal
        self._sizes: list[tuple[int, int] | None] = []  # logical; None if not reported
        self._frames: dict[int, Image.Image] = {}

    # -- FrameSource -------------------------------------------------------------------

    async def start(self) -> None:
        """Run the first session attempt (which may wait on the user's portal dialog).

        Returns once that attempt has succeeded or failed; failures and later stream
        errors are retried with backoff in the background until close(), except a
        user-cancelled permission dialog, which stops all further attempts.
        """
        if self._task is None:
            Gst.init(None)
            self._first_attempt.clear()
            self._task = asyncio.create_task(self._run())
        try:
            await self._first_attempt.wait()
        except asyncio.CancelledError:
            await self.close()
            raise

    def latest_frame(self) -> Image.Image | None:
        with self._lock:
            if not self._positions or len(self._frames) < len(self._positions):
                return None
            frames = [
                (self._frames[i], pos, self._sizes[i] or self._frames[i].size)
                for i, pos in enumerate(self._positions)
            ]
        return composite(layout_frames(frames))

    async def close(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await self._teardown()

    # -- supervision -------------------------------------------------------------------

    async def _run(self) -> None:
        failures = 0
        try:
            while True:
                try:
                    await self._open()
                except Exception as exc:
                    await self._teardown()
                    self._first_attempt.set()
                    if not should_retry(exc):
                        log.warning(
                            "screen capture permission was cancelled; "
                            "restart the daemon to be asked again"
                        )
                        return
                    log.warning("screen capture unavailable: %s", _describe(exc))
                    failures += 1
                    await asyncio.sleep(backoff_delay(failures))
                    continue

                self.available = True
                self._first_attempt.set()
                started = time.monotonic()
                log.info("screen capture started (%d stream(s))", len(self._pipelines))
                reason = await self._watch()
                log.warning("screen capture stopped: %s", reason)
                self.available = False
                await self._teardown()
                if time.monotonic() - started >= _STABLE_SESSION_SECONDS:
                    failures = 0
                failures += 1
                await asyncio.sleep(backoff_delay(failures))
        finally:
            self.available = False
            self._first_attempt.set()
            await self._teardown()

    async def _open(self) -> None:
        token = self._restore_token
        try:
            await self._open_session(token)
        except PortalResponseError as exc:
            if token is None or exc.response == RESPONSE_CANCELLED:
                raise
            log.info("portal rejected the saved restore token; asking again without it")
            self._restore_token = None
            await self._teardown()
            await self._open_session(None)

    async def _watch(self) -> str:
        """Wait until a pipeline reports ERROR/EOS or the portal session goes away."""
        while True:
            for pipeline in self._pipelines:
                msg = pipeline.get_bus().timed_pop_filtered(
                    0, Gst.MessageType.ERROR | Gst.MessageType.EOS
                )
                if msg is None:
                    continue
                if msg.type == Gst.MessageType.ERROR:
                    err, _debug = msg.parse_error()
                    return f"pipeline error: {err.message}"
                return "stream ended"
            if self._session_closed:
                return "portal session closed"
            if self._bus is None or not self._bus.connected:
                return "D-Bus connection lost"
            await asyncio.sleep(_WATCH_INTERVAL)

    # -- portal --------------------------------------------------------------------------

    async def _open_session(self, restore_token: str | None) -> None:
        self._session_closed = False
        self._bus = bus = await MessageBus(
            bus_type=BusType.SESSION, negotiate_unix_fd=True
        ).connect()

        results = await self._request(
            "CreateSession", "a{sv}",
            [{"session_handle_token": Variant("s", _new_token())}],
        )
        self._session = session = results["session_handle"].value
        await self._watch_session_closed(session)

        options = {
            "types": Variant("u", SOURCE_TYPE_MONITOR),
            "multiple": Variant("b", True),
            "cursor_mode": Variant("u", CURSOR_MODE_EMBEDDED),
            "persist_mode": Variant("u", PERSIST_MODE_UNTIL_REVOKED),
        }
        if restore_token:
            options["restore_token"] = Variant("s", restore_token)
        await self._request("SelectSources", "oa{sv}", [session, options])

        results = await self._request("Start", "osa{sv}", [session, "", {}])
        if "restore_token" in results:
            self._restore_token = results["restore_token"].value
            try:
                self._on_restore_token(self._restore_token)
            except Exception as exc:
                log.warning("could not save the portal restore token: %s", _describe(exc))
        streams = results["streams"].value if "streams" in results else []
        if not streams:
            raise PortalError("portal Start returned no streams")

        reply = await bus.call(Message(
            destination=PORTAL_BUS_NAME, path=PORTAL_PATH, interface=SCREENCAST_IFACE,
            member="OpenPipeWireRemote", signature="oa{sv}", body=[session, {}],
        ))
        _check_reply(reply, "OpenPipeWireRemote")
        self._fd = reply.unix_fds[reply.body[0]]

        self._start_pipelines(streams)

    async def _request(self, method: str, signature: str, body: list) -> dict[str, Variant]:
        """Call a ScreenCast method and wait for its Request.Response; return the results."""
        bus = self._bus
        handle_token = _new_token()
        body[-1]["handle_token"] = Variant("s", handle_token)
        path = request_path(bus.unique_name, handle_token)
        rule = f"type='signal',interface='{REQUEST_IFACE}',member='Response',path='{path}'"
        response: asyncio.Future = asyncio.get_running_loop().create_future()

        def on_message(msg: Message) -> None:
            if (msg.message_type == MessageType.SIGNAL and msg.path == path
                    and msg.interface == REQUEST_IFACE and msg.member == "Response"
                    and not response.done()):
                response.set_result(msg.body)

        # Subscribe before calling so a fast Response cannot be missed.
        bus.add_message_handler(on_message)
        try:
            await _bus_match(bus, "AddMatch", rule)
            reply = await bus.call(Message(
                destination=PORTAL_BUS_NAME, path=PORTAL_PATH, interface=SCREENCAST_IFACE,
                member=method, signature=signature, body=body,
            ))
            _check_reply(reply, method)
            if reply.body[0] != path:
                raise PortalError(f"portal {method} returned an unexpected request path")
            code, results = await response
        finally:
            bus.remove_message_handler(on_message)
            if bus.connected:
                try:
                    await _bus_match(bus, "RemoveMatch", rule)
                except Exception:
                    pass
        if code != 0:
            raise PortalResponseError(method, code)
        return results

    async def _watch_session_closed(self, session: str) -> None:
        def on_message(msg: Message) -> None:
            if (msg.message_type == MessageType.SIGNAL and msg.path == session
                    and msg.interface == SESSION_IFACE and msg.member == "Closed"):
                self._session_closed = True

        self._bus.add_message_handler(on_message)
        await _bus_match(
            self._bus, "AddMatch",
            f"type='signal',interface='{SESSION_IFACE}',member='Closed',path='{session}'",
        )

    # -- GStreamer -----------------------------------------------------------------------

    def _start_pipelines(self, streams: list) -> None:
        positions, sizes = [], []
        for node_id, props in streams:
            pos = props["position"].value if "position" in props else (0, 0)
            positions.append((int(pos[0]), int(pos[1])))
            size = props["size"].value if "size" in props else None
            sizes.append((int(size[0]), int(size[1])) if size and min(size) > 0 else None)
        with self._lock:
            self._positions = positions
            self._sizes = sizes
            self._frames = {}

        for index, (node_id, _props) in enumerate(streams):
            pipeline = Gst.parse_launch(pipeline_description(self._fd, node_id, self._max_fps))
            self._pipelines.append(pipeline)
            pipeline.get_by_name("sink").connect("new-sample", self._on_sample, index)
            if pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
                raise PortalError(f"could not start the pipeline for stream {index}")

    def _on_sample(self, sink, index: int) -> Gst.FlowReturn:
        # GStreamer streaming thread: no asyncio here, and never log image data.
        sample = sink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.OK
        try:
            image = _sample_to_image(sample)
        except Exception as exc:
            log.debug("dropping a frame that could not be converted: %s", _describe(exc))
            return Gst.FlowReturn.OK
        with self._lock:
            if index < len(self._positions):
                self._frames[index] = image
        return Gst.FlowReturn.OK

    # -- teardown ------------------------------------------------------------------------

    async def _teardown(self) -> None:
        self.available = False
        pipelines, self._pipelines = self._pipelines, []
        for pipeline in pipelines:
            pipeline.set_state(Gst.State.NULL)
        with self._lock:
            self._positions = []
            self._sizes = []
            self._frames = {}
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

        bus, self._bus = self._bus, None
        session, self._session = self._session, None
        if bus is None:
            return
        if session is not None and bus.connected:
            try:
                await asyncio.wait_for(bus.call(Message(
                    destination=PORTAL_BUS_NAME, path=session,
                    interface=SESSION_IFACE, member="Close",
                )), _CLOSE_TIMEOUT)
            except Exception as exc:
                log.debug("closing the portal session failed: %s", _describe(exc))
        bus.disconnect()


def _sample_to_image(sample) -> Image.Image:
    caps = sample.get_caps()
    info = GstVideo.VideoInfo.new_from_caps(caps)
    buffer = sample.get_buffer()
    meta = GstVideo.buffer_get_video_meta(buffer)
    offset, stride = (meta.offset[0], meta.stride[0]) if meta else (info.offset[0], info.stride[0])
    ok, mapinfo = buffer.map(Gst.MapFlags.READ)
    if not ok:
        raise PortalError("could not map the frame buffer")
    try:
        data = bytes(mapinfo.data)[offset:]
        return rgb_image_from_bytes(data, info.width, info.height, stride)
    finally:
        buffer.unmap(mapinfo)


async def _bus_match(bus: MessageBus, member: str, rule: str) -> None:
    reply = await bus.call(Message(
        destination="org.freedesktop.DBus", path="/org/freedesktop/DBus",
        interface="org.freedesktop.DBus", member=member, signature="s", body=[rule],
    ))
    _check_reply(reply, member)


def _check_reply(reply: Message, method: str) -> None:
    if reply.message_type == MessageType.ERROR:
        raise PortalError(f"{method} failed: {reply.error_name}")


def _new_token() -> str:
    return f"vitality_{secrets.token_hex(8)}"


def _describe(exc: BaseException) -> str:
    return str(exc) or type(exc).__name__
