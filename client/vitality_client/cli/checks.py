"""Setup checks behind `vitality-client doctor`.

Each check is a small function over injectable inputs; run_checks() wires in the
real environment. Nothing here opens a capture session or shows a portal dialog.
"""

import asyncio
import grp
import json
import os
import subprocess
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

import httpx
from vitality_protocol.messages import Health
from vitality_protocol.routes import HEALTH_PATH

from vitality_client.shared.config import Paths, load_config


# == Contract =============================================================================

INPUT_GROUP_HINT = "sudo usermod -aG input $USER, then log out and back in"
PORTAL_HINT = ("install xdg-desktop-portal and a ScreenCast backend for your compositor "
               "(e.g. xdg-desktop-portal-hyprland), and make sure it is running")
GI_HINT = ("sudo pacman -S python-gobject (and the virtualenv must use system site packages: "
           "uv venv --python /usr/bin/python3 --system-site-packages)")
TAILSCALE_HINT = "start Tailscale: sudo systemctl enable --now tailscaled && sudo tailscale up"
GST_HINT = "sudo pacman -S gst-plugins-base gst-plugin-pipewire"


@dataclass
class Check:
    """One line of `doctor`: what was checked, whether it passed, and what to do if not."""

    name: str
    ok: bool
    hint: str


def current_groups() -> list[str]:
    """Names of the groups this process is in (so a fresh `usermod` shows only after re-login)."""
    names = []
    for gid in os.getgroups():
        try:
            names.append(grp.getgrgid(gid).gr_name)
        except KeyError:
            pass
    return names


def check_input_group(groups: Iterable[str]) -> Check:
    """Passes if `input` is among `groups` (needed to read keyboard and mouse events)."""
    ok = "input" in set(groups)
    return Check("input group", ok, "" if ok else INPUT_GROUP_HINT)


def check_wayland(env: Mapping[str, str]) -> Check:
    """Passes if `XDG_SESSION_TYPE` in `env` is `wayland`."""
    session = env.get("XDG_SESSION_TYPE", "")
    ok = session == "wayland"
    return Check("wayland session", ok, "" if ok else
                 f"XDG_SESSION_TYPE={session}; log in to a Wayland session")


def check_portal(probe: Callable[[], bool]) -> Check:
    """Passes if `probe()` is true; a probe that raises fails, with its message in the hint."""
    return _probe_check("portal", probe, PORTAL_HINT)


def check_tailscale(probe: Callable[[], bool]) -> Check:
    """Passes if `probe()` is true; a probe that raises fails, with its message in the hint."""
    return _probe_check("tailscale", probe, TAILSCALE_HINT)


def check_upload_server(server: str, probe: Callable[[str], bool]) -> Check:
    """Passes if `probe(server)` is true; a probe that raises fails, with its message in
    the hint."""
    return _probe_check("upload server", lambda: probe(server),
                        f"cannot reach {server}{HEALTH_PATH} over Tailscale; "
                        "is vitality-server running?")


def check_pipewire(env: Mapping[str, str]) -> Check:
    """Passes if PipeWire's socket exists in `XDG_RUNTIME_DIR` of `env`."""
    runtime = env.get("XDG_RUNTIME_DIR")
    ok = bool(runtime) and (Path(runtime) / "pipewire-0").exists()
    return Check("pipewire", ok, "" if ok else
                 "PipeWire is not running: systemctl --user enable --now pipewire")


def check_gi(probe: Callable[[], bool]) -> Check:
    """Passes if `probe()` is true; a probe that raises fails, with its message in the hint."""
    return _probe_check("python gobject", probe, GI_HINT)


def check_gst_elements(probe: Callable[[], Iterable[str]]) -> Check:
    """`probe` returns the names of the pipeline's GStreamer elements that are missing.

    Passes if it returns none; fails naming the missing ones, or with the probe's message
    if it raises.
    """
    name = "gstreamer elements"
    try:
        missing = list(probe())
    except Exception as exc:
        return Check(name, False, f"{GST_HINT} ({str(exc) or type(exc).__name__})")
    if not missing:
        return Check(name, True, "")
    return Check(name, False, f"missing {', '.join(missing)}: {GST_HINT}")


def check_directories(paths: Paths) -> Check:
    """Passes if the data, state and socket directories are writable or can be created."""
    dirs = [paths.data_dir, paths.state_file.parent, paths.socket_path.parent]
    bad = [str(d) for d in dirs if not _writable(d)]
    return Check("directories writable", not bad, "" if not bad else
                 "not writable: " + ", ".join(bad))


def portal_has_screencast() -> bool:
    """Whether the desktop portal offers ScreenCast, by introspecting it on the session bus;
    opens no session and shows no dialog. Raises if the bus or portal cannot be reached
    within 5 seconds."""
    from dbus_fast import BusType
    from dbus_fast.aio import MessageBus

    async def probe() -> bool:
        bus = await MessageBus(bus_type=BusType.SESSION).connect()
        try:
            node = await bus.introspect(PORTAL_BUS_NAME, PORTAL_PATH)
            return any(i.name == SCREENCAST_IFACE for i in node.interfaces)
        finally:
            bus.disconnect()

    return asyncio.run(asyncio.wait_for(probe(), PROBE_TIMEOUT))


def gi_importable() -> bool:
    """True if Python's `gi` (GObject) imports; raises ImportError if not."""
    import gi  # noqa: F401  (ImportError is reported by the check)

    return True


def missing_gst_elements() -> list[str]:
    """The GStreamer elements the capture pipeline needs that are not installed.

    Raises ImportError if `gi` is missing.
    """
    import gi

    gi.require_version("Gst", "1.0")
    from gi.repository import Gst

    Gst.init(None)
    return [e for e in GST_ELEMENTS if Gst.ElementFactory.find(e) is None]


def tailscale_running() -> bool:
    """`tailscale status --json` reports BackendState "Running"; False if it can't be asked."""
    try:
        result = subprocess.run(["tailscale", "status", "--json"], capture_output=True,
                                timeout=PROBE_TIMEOUT)
        state = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        return False
    return (result.returncode == 0 and isinstance(state, dict)
            and state.get("BackendState") == "Running")


def server_healthy(server: str) -> bool:
    """Whether the server answers `GET /health` with 200 and a valid `Health` body; False
    on any network failure or bad reply."""
    try:
        response = httpx.get(server + HEALTH_PATH, timeout=HEALTH_TIMEOUT)
        Health.model_validate_json(response.content)
    except (httpx.HTTPError, ValueError):  # ValueError: pydantic's ValidationError
        return False
    return response.status_code == 200


def run_checks(
    env: Mapping[str, str],
    paths: Paths,
    groups: Iterable[str] | None = None,
    portal_probe: Callable[[], bool] = portal_has_screencast,
    gi_probe: Callable[[], bool] = gi_importable,
    gst_probe: Callable[[], Iterable[str]] = missing_gst_elements,
    tailscale_probe: Callable[[], bool] = tailscale_running,
    health_probe: Callable[[str], bool] = server_healthy,
) -> list[Check]:
    """Run every setup check; each probe argument replaces the real environment in tests.

    Returns, in this order: input group, wayland session, portal, pipewire, python gobject,
    gstreamer elements, directories writable; then, if the config names an `upload_server`,
    tailscale and the upload server. An unreadable or invalid config adds one failing
    `config` check instead and stops there. Never raises for a failing check.
    """
    gi_check = check_gi(gi_probe)
    # Without gi the element probe can only fail with the same ImportError; say so once.
    gst_check = (check_gst_elements(gst_probe) if gi_check.ok else
                 Check("gstreamer elements", False, "needs python gobject (see above)"))
    checks = [
        check_input_group(current_groups() if groups is None else groups),
        check_wayland(env),
        check_portal(portal_probe),
        check_pipewire(env),
        gi_check,
        gst_check,
        check_directories(paths),
    ]
    try:
        server = load_config(paths.config_file).upload_server
    except (ValueError, OSError) as exc:
        return [*checks, Check("config", False, str(exc))]
    if server is not None:
        checks += [check_tailscale(tailscale_probe), check_upload_server(server, health_probe)]
    return checks


# == Implementation =======================================================================

PORTAL_BUS_NAME = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
SCREENCAST_IFACE = "org.freedesktop.portal.ScreenCast"
PROBE_TIMEOUT = 5.0
HEALTH_TIMEOUT = 5.0
# Every element the capture pipeline (frames/portal.py pipeline_description) uses.
GST_ELEMENTS = ("pipewiresrc", "appsink")


def _probe_check(name: str, probe: Callable[[], bool], hint: str) -> Check:
    try:
        ok = bool(probe())
    except Exception as exc:
        return Check(name, False, f"{hint} ({str(exc) or type(exc).__name__})")
    return Check(name, ok, "" if ok else hint)


def _writable(directory: Path) -> bool:
    """Writable now, or creatable: the nearest existing ancestor is a writable directory."""
    for candidate in (directory, *directory.parents):
        if candidate.exists():
            return candidate.is_dir() and os.access(candidate, os.W_OK | os.X_OK)
    return False
