import subprocess
import sys


def test_stream_keeps_our_lines_before_the_child_s_output(tmp_path):
    """Printed lines must reach a pipe or file before the child command's output does."""
    out = tmp_path / "out.txt"
    script = (
        "from vitality_client.cli.commands import stream\n"
        "print('==> before')\n"
        "stream(['echo', 'child'], None)\n"
        "print('==> after')\n"
    )
    with out.open("w") as f:
        subprocess.run([sys.executable, "-c", script], stdout=f, check=True)
    assert out.read_text().splitlines() == ["==> before", "child", "==> after"]
