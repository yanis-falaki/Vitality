"""The setup checks behind `vitality-server doctor`.

Each check is a small function over an injectable probe; `run_checks` wires in the real
machine. A probe that raises makes its check fail, with the reason added to the hint. Nothing
here changes anything on the machine.
"""

import asyncio
import shutil
import subprocess
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from vitality_protocol.messages import Health
from vitality_protocol.routes import HEALTH_PATH

from vitality_server.cli.display import format_bytes
from vitality_server.shared.config import ServerConfig, load_server_config
from vitality_server.shared.identity import tailscale_ipv4

NEEDS_CONFIG = "needs a valid config"
TAILSCALE_HINT = "sudo tailscale up --hostname=vitality-librarian"
SERVICE_HINT = "sudo systemctl start vitality-serverd; logs: journalctl -u vitality-serverd -n 50"
HEALTH_HINT = "is the service running? see journalctl -u vitality-serverd"
DISK_HINT = "grow the VM's disk"
MIN_FREE_BYTES = 1_000_000_000
"""Below this much free space, `disk space` fails."""
PROBE_TIMEOUT = 5.0


@dataclass
class Check:
    """One line of `doctor`: what was checked, whether it passed, and what to do if not."""

    name: str
    ok: bool
    hint: str


def tailscale_address() -> str:
    """This machine's Tailscale IPv4 address; raises RuntimeError (saying why) if it has none."""
    return asyncio.run(tailscale_ipv4())


def service_active() -> bool:
    """Whether `systemctl is-active --quiet vitality-serverd` succeeds; False if it cannot be asked."""
    try:
        result = subprocess.run(["systemctl", "is-active", "--quiet", "vitality-serverd"],
                                capture_output=True, timeout=PROBE_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def server_healthy(ip: str, port: int) -> bool:
    """Whether `GET http://ip:port/health` answers 200 with a valid `Health` body; False on
    any other status or a body that is not one."""
    url = f"http://{ip}:{port}{HEALTH_PATH}"
    with urllib.request.urlopen(url, timeout=PROBE_TIMEOUT) as response:  # raises on 4xx/5xx
        body = response.read()
        ok = response.status == 200
    try:
        Health.model_validate_json(body)
    except ValueError:  # pydantic's ValidationError
        return False
    return ok


def disk_free_bytes(data_dir: Path) -> int:
    """Free bytes on the filesystem holding `data_dir`; raises OSError if it cannot be read."""
    return shutil.disk_usage(data_dir).free


def run_checks(
    config_path: Path,
    tailscale_probe: Callable[[], str] = tailscale_address,
    service_probe: Callable[[], bool] = service_active,
    health_probe: Callable[[str, int], bool] = server_healthy,
    disk_probe: Callable[[Path], int] = disk_free_bytes,
) -> list[Check]:
    """Run every check; each probe argument replaces the real machine in tests.

    Returns, in this order: config loads (named `config loads (no file; using defaults)` when
    the file does not exist, which is allowed but worth seeing), tailscale up (<ip>), service running, /health
    answers on <ip>:<port>, data folder present, allowed_clients set (<names>), disk space
    (<free> free). `tailscale_probe` returns the address or raises; `health_probe` gets that
    address and the configured port; `disk_probe` gets the data folder. If the config does not
    load, the checks that need it (/health, data folder, allowed_clients, disk) fail with
    "needs a valid config"; if there is no Tailscale address, /health fails saying so. Never
    raises for a failing check.
    """
    try:
        config = load_server_config(config_path)
    except (ValueError, OSError) as exc:
        config, config_check = None, Check("config loads", False, str(exc))
    else:
        defaults = "" if config_path.exists() else " (no file; using defaults)"
        config_check = Check(f"config loads{defaults}", True, "")
    address = _address(tailscale_probe)
    return [
        config_check,
        address.check,
        _probe_check("service running", service_probe, SERVICE_HINT),
        _check_health(config, address, health_probe),
        _check_data_folder(config),
        _check_allowed_clients(config, config_path),
        _check_disk(config, disk_probe),
    ]


@dataclass
class _Address:
    """The result of the tailscale probe: its check, and the address if it found one."""

    check: Check
    ip: str | None


def _address(probe: Callable[[], str]) -> _Address:
    try:
        ip = probe()
    except Exception as exc:
        return _Address(Check("tailscale up", False, _with_reason(TAILSCALE_HINT, exc)), None)
    return _Address(Check(f"tailscale up ({ip})", True, ""), ip)


def _probe_check(name: str, probe: Callable[[], bool], hint: str) -> Check:
    try:
        ok = bool(probe())
    except Exception as exc:
        return Check(name, False, _with_reason(hint, exc))
    return Check(name, ok, "" if ok else hint)


def _check_health(config: ServerConfig | None, address: _Address,
                  probe: Callable[[str, int], bool]) -> Check:
    name = "/health answers"
    if config is None:
        return Check(name, False, NEEDS_CONFIG)
    if address.ip is None:
        return Check(name, False, "needs tailscale up (see above)")
    ip, port = address.ip, config.port
    return _probe_check(f"{name} on {ip}:{port}", lambda: probe(ip, port), HEALTH_HINT)


def _check_data_folder(config: ServerConfig | None) -> Check:
    name = "data folder present"
    if config is None:
        return Check(name, False, NEEDS_CONFIG)
    ok = config.data_dir.is_dir()
    return Check(name, ok, "" if ok else
                 f"{config.data_dir} is missing; install.sh creates it (rerun ./server/install.sh)")


def _check_allowed_clients(config: ServerConfig | None, config_path: Path) -> Check:
    name = "allowed_clients set"
    if config is None:
        return Check(name, False, NEEDS_CONFIG)
    if not config.allowed_clients:
        return Check(name, False,
                     f"edit {config_path}, then sudo systemctl restart vitality-serverd")
    return Check(f"{name} ({', '.join(config.allowed_clients)})", True, "")


def _check_disk(config: ServerConfig | None, probe: Callable[[Path], int]) -> Check:
    name = "disk space"
    if config is None:
        return Check(name, False, NEEDS_CONFIG)
    try:
        free = probe(config.data_dir)
    except Exception as exc:
        return Check(name, False, _with_reason(DISK_HINT, exc))
    ok = free >= MIN_FREE_BYTES
    return Check(f"{name} ({format_bytes(free)} free)", ok, "" if ok else DISK_HINT)


def _with_reason(hint: str, exc: Exception) -> str:
    return f"{hint} ({str(exc) or type(exc).__name__})"
