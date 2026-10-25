"""The block of paths that `status` and `doctor` end with: where everything lives."""

from pathlib import Path

PROGRAM_DIR = Path("/opt/vitality-server")
"""Where `install.sh` puts the server's virtual environment."""

SERVICE_FILE = Path("/etc/systemd/system/vitality-server.service")
"""The installed systemd unit."""

LOGS_COMMAND = "journalctl -u vitality-server"
"""How to read the server's logs."""


def path_lines(config_path: Path, data_dir: Path | None) -> list[str]:
    """The lines `config:`, `data:`, `captures:`, `index:`, `program:`, `service:` and
    `logs:`, labels aligned.

    `data_dir` is None when the settings could not be loaded: the lines that depend on it
    (`data`, `captures`, `index`) are then left out.
    """
    rows = [("config", str(config_path))]
    if data_dir is not None:
        rows += [("data", str(data_dir)), ("captures", str(data_dir / "captures")),
                 ("index", str(data_dir / "index.sqlite"))]
    rows += [("program", str(PROGRAM_DIR)), ("service", str(SERVICE_FILE)), ("logs", LOGS_COMMAND)]
    return [f"{label + ':':<10}{value}" for label, value in rows]
