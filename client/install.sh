#!/usr/bin/env bash
# Install or upgrade the Vitality client (the laptop daemon) for this user.
#
# Run it as your normal user (no sudo), from the root of the repo:
#   ./client/install.sh
# It builds a separate, non-editable copy of the program in ~/.local/share/vitality-client,
# so editing the repo never changes the running daemon. Rerunning is safe: it stops the
# service, replaces the program, and starts the service again. (`vitality-client upgrade`
# runs this for you from the repo's main.)
set -euo pipefail

APP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/vitality-client"   # the installed program
VENV="$APP_DIR/venv"
BIN_DIR="$HOME/.local/bin"
UNIT=vitality-clientd.service
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

if [[ ! -d protocol || ! -d client || ! -f protocol/pyproject.toml || ! -f client/pyproject.toml ]]; then
    echo "usage: run ./client/install.sh from the root of the Vitality repo (the directory with protocol/ and client/)" >&2
    exit 1
fi
if [[ $EUID -eq 0 ]]; then
    echo "run this as your normal user; this installs into your home" >&2
    exit 1
fi

# Prerequisites: each one that is missing says how to fix it.
if ! /usr/bin/python3 -c 'import gi; gi.require_version("Gst", "1.0"); from gi.repository import Gst' >/dev/null 2>&1; then
    echo "the system Python cannot import GStreamer (python-gobject, gst-plugins-base)." >&2
    echo "  fix: sudo pacman -S python-gobject gst-plugins-base gst-plugin-pipewire" >&2
    exit 1
fi
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
    echo "uv is not installed (it builds the environment)." >&2
    echo "  fix: curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
    exit 1
fi
if ! id -nG | tr ' ' '\n' | grep -qx input; then
    echo "warning: you are not in the input group; the daemon cannot see keyboard and mouse activity." >&2
    echo "         Run: sudo usermod -aG input \$USER, then log out and in" >&2
fi

# On exit: delete the temporary requirements file, and if the script stopped the service and
# then failed, start it again so a failed install does not leave the daemon down.
REQUIREMENTS=""
STOPPED=0
cleanup() {
    local status=$?
    [[ -z "$REQUIREMENTS" ]] || rm -f "$REQUIREMENTS"
    if [[ $status -ne 0 && $STOPPED -eq 1 ]]; then
        systemctl --user start "$UNIT" || true
        echo "install failed; restarted the previous vitality-clientd (it may be running partly replaced files: rerun ./client/install.sh)" >&2
    fi
}
trap cleanup EXIT

# Stop the running daemon before its files are replaced (the first install has no unit yet).
if systemctl --user is-active --quiet "$UNIT"; then
    echo "==> stopping vitality-clientd while the program is replaced"
    STOPPED=1   # set first: if the stop half-works, a failure still tries to start it
    systemctl --user stop "$UNIT"
fi

echo "==> installing the client into $VENV"
# System site packages: the system PyGObject/GStreamer must be importable from the venv.
[[ -x "$VENV/bin/python" ]] || uv venv --python /usr/bin/python3 --system-site-packages "$VENV"
# The third-party dependencies at exactly the versions in client/uv.lock, with their hashes
# (uv checks every hash given). --locked stops here if the lock is out of date with
# client/pyproject.toml. The two local packages are left out of the export: they have no
# hash and are installed from this checkout below.
REQUIREMENTS=$(mktemp)
(cd client && uv export --locked --no-dev --no-editable --no-emit-project --no-emit-local \
    --quiet) > "$REQUIREMENTS"
# Reinstall both packages every time: the version number does not change between upgrades.
# --no-sources: ignore client/pyproject.toml's editable path source for vitality-protocol, so
# both are copied into the venv and nothing installed depends on this checkout.
uv pip install --python "$VENV/bin/python" --no-sources -r "$REQUIREMENTS" \
    --reinstall-package vitality-protocol --refresh-package vitality-protocol \
    --reinstall-package vitality-client --refresh-package vitality-client \
    ./protocol ./client
# Remember which checkout and commit this came from: `vitality-client upgrade` reinstalls
# from there, and skips the install when the commit has not changed.
mkdir -p "$APP_DIR"
pwd -P > "$APP_DIR/source"
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    git rev-parse HEAD > "$APP_DIR/installed"
else
    rm -f "$APP_DIR/installed"
fi

echo "==> linking the commands"
# vitality-clientd is the daemon (run by systemd); vitality-client controls it.
mkdir -p "$BIN_DIR"
ln -sfn "$VENV/bin/vitality-client" "$BIN_DIR/vitality-client"
ln -sfn "$VENV/bin/vitality-clientd" "$BIN_DIR/vitality-clientd"

echo "==> installing the service"
mkdir -p "$UNIT_DIR"
# An older setup linked the unit into the repo; installing over the link would write into the repo.
if [[ -L "$UNIT_DIR/$UNIT" ]]; then
    rm "$UNIT_DIR/$UNIT"
fi
install -m 644 client/systemd/"$UNIT" "$UNIT_DIR/$UNIT"
systemctl --user daemon-reload
if [[ $STOPPED -eq 1 ]]; then
    systemctl --user start "$UNIT"
    STOPPED=0   # started again: a failure from here on needs no restart
else
    echo "    not started: it starts at your next login (Hyprland starts it), or now with:"
    echo "      systemctl --user start vitality-clientd"
fi

cat <<NEXT

Done. Next:
  1. Check the setup: vitality-client doctor

Later, to update: commit or merge to main in this repo, then run: vitality-client upgrade
(client/.venv is now only for development: uv run pytest)
NEXT
