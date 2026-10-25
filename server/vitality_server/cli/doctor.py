"""`vitality-server doctor`: check the setup, then show where everything lives."""

from pathlib import Path

from vitality_server.cli.checks import run_checks
from vitality_server.cli.paths import path_lines
from vitality_server.config import load_server_config


def doctor(config_path: Path) -> int:
    """Print one line per setup check (`✓ name`, or `✗ name — what to do`), then the paths
    block that `status` ends with.

    If the settings cannot be loaded the block has no `data`, `captures` or `index` lines
    (they depend on `data_dir`). Returns 0 if every check passed, else 1.
    """
    checks = run_checks(config_path)
    for c in checks:
        print(f"{'✓' if c.ok else '✗'} {c.name}" + ("" if c.ok else f" — {c.hint}"))
    try:
        data_dir = load_server_config(config_path).data_dir
    except (ValueError, OSError):
        data_dir = None
    print("\n".join(path_lines(config_path, data_dir)))
    return 0 if all(c.ok for c in checks) else 1
