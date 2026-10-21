"""Setup checks behind `vitality-client doctor`.

Each check is a small function over injectable inputs; run_checks() wires in the
real environment. Nothing here opens a capture session or shows a portal dialog.
"""

import asyncio
import grp
import os
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from vitality_client.config import Paths

PORTAL_BUS_NAME = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
SCREENCAST_IFACE = "org.freedesktop.portal.ScreenCast"
PROBE_TIMEOUT = 5.0

INPUT_GROUP_HINT = "sudo usermod -aG input $USER, then log out and back in"
PORTAL_HINT = ("install xdg-desktop-portal and a ScreenCast backend for your compositor "
               "(e.g. xdg-desktop-portal-hyprland), and make sure it is running")
GST_HINT = "sudo pacman -S gst-plugin-pipewire (or the distro equivalent)"


@dataclass
class Check:
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
    ok = "input" in set(groups)
    return Check("input group", ok, "" if ok else INPUT_GROUP_HINT)


def check_wayland(env: Mapping[str, str]) -> Check:
    session = env.get("XDG_SESSION_TYPE", "")
    ok = session == "wayland"
    return Check("wayland session", ok, "" if ok else
                 f"XDG_SESSION_TYPE={session}; log in to a Wayland session")


def _probe_check(name: str, probe: Callable[[], bool], hint: str) -> Check:
    try:
        ok = bool(probe())
    except Exception as exc:
        return Check(name, False, f"{hint} ({str(exc) or type(exc).__name__})")
    return Check(name, ok, "" if ok else hint)


def check_portal(probe: Callable[[], bool]) -> Check:
    return _probe_check("portal", probe, PORTAL_HINT)


def check_pipewire(env: Mapping[str, str]) -> Check:
    runtime = env.get("XDG_RUNTIME_DIR")
    ok = bool(runtime) and (Path(runtime) / "pipewire-0").exists()
    return Check("pipewire", ok, "" if ok else
                 "PipeWire is not running: systemctl --user enable --now pipewire")


def check_gst_plugin(probe: Callable[[], bool]) -> Check:
    return _probe_check("gstreamer pipewire plugin", probe, GST_HINT)


def _writable(directory: Path) -> bool:
    """Writable now, or creatable: the nearest existing ancestor is a writable directory."""
    for candidate in (directory, *directory.parents):
        if candidate.exists():
            return candidate.is_dir() and os.access(candidate, os.W_OK | os.X_OK)
    return False


def check_directories(paths: Paths) -> Check:
    dirs = [paths.data_dir, paths.state_file.parent, paths.socket_path.parent]
    bad = [str(d) for d in dirs if not _writable(d)]
    return Check("directories writable", not bad, "" if not bad else
                 "not writable: " + ", ".join(bad))


def portal_has_screencast() -> bool:
    """Introspect the portal on the session bus; opens no session and shows no dialog."""
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


def gst_has_pipewire() -> bool:
    import gi

    gi.require_version("Gst", "1.0")
    from gi.repository import Gst

    Gst.init(None)
    return Gst.ElementFactory.find("pipewiresrc") is not None


def run_checks(
    env: Mapping[str, str],
    paths: Paths,
    groups: Iterable[str] | None = None,
    portal_probe: Callable[[], bool] = portal_has_screencast,
    gst_probe: Callable[[], bool] = gst_has_pipewire,
) -> list[Check]:
    return [
        check_input_group(current_groups() if groups is None else groups),
        check_wayland(env),
        check_portal(portal_probe),
        check_pipewire(env),
        check_gst_plugin(gst_probe),
        check_directories(paths),
    ]
