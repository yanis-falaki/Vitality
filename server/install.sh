#!/usr/bin/env bash
# Install or upgrade the Vitality server on this machine.
#
# Run it as your normal user (it uses sudo where it must), from the root of the repo:
#   ./server/install.sh
# Rerunning is safe: it stops the service, upgrades the code, keeps /etc/vitality/server.toml,
# and starts the service again. (`vitality-server upgrade` pulls the repo and runs this for you.)
set -euo pipefail

APP_DIR=/opt/vitality-server            # the server's virtual environment
PYTHON_DIR=/opt/vitality-python         # uv's Python, kept where the vitality user can read it
DATA_DIR=/srv/vitality
CONFIG_DIR=/etc/vitality
UNIT=vitality-serverd.service

if [[ ! -d protocol || ! -d server || ! -f protocol/pyproject.toml || ! -f server/pyproject.toml ]]; then
    echo "usage: run ./server/install.sh from the root of the Vitality repo (the directory with protocol/ and server/)" >&2
    exit 1
fi

# uv: use the one on the machine, else install it for this user.
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
    echo "==> installing uv"
    curl -LsSf https://astral.sh/uv/install.sh | sh
fi
command -v uv >/dev/null 2>&1 || { echo "uv is not on PATH after installing it" >&2; exit 1; }

# On exit: delete the temporary requirements file, and if the script stopped the service and
# then failed, start it again so a failed install does not leave the server down.
REQUIREMENTS=""
STOPPED=0
cleanup() {
    local status=$?
    [[ -z "$REQUIREMENTS" ]] || rm -f "$REQUIREMENTS"
    if [[ $status -ne 0 && $STOPPED -eq 1 ]]; then
        sudo systemctl start "$UNIT" || true
        echo "install failed; restarted the previous vitality-serverd (it may be running partly replaced files: rerun ./server/install.sh)" >&2
    fi
}
trap cleanup EXIT

# Stop the running server before its files are replaced (the first install has no unit yet).
if systemctl is-active --quiet "$UNIT"; then
    echo "==> stopping vitality-serverd while the program is replaced"
    STOPPED=1   # set first: if the stop half-works, a failure still tries to start it
    sudo systemctl stop "$UNIT"
fi

echo "==> installing Python 3.14 and the server into $APP_DIR"
export UV_PYTHON_INSTALL_DIR="$PYTHON_DIR"
sudo install -d -m 755 -o "$(id -un)" -g "$(id -gn)" "$APP_DIR" "$PYTHON_DIR"
uv python install 3.14
[[ -x "$APP_DIR/bin/python" ]] || uv venv --python 3.14 "$APP_DIR"
# The third-party dependencies at exactly the versions in server/uv.lock, with their hashes
# (uv checks every hash given). --locked stops here if the lock is out of date with
# server/pyproject.toml. The two local packages are left out of the export: they have no
# hash and are installed from this checkout below.
REQUIREMENTS=$(mktemp)
(cd server && uv export --locked --no-dev --no-editable --no-emit-project --no-emit-local \
    --quiet) > "$REQUIREMENTS"
# Reinstall both packages every time: the version number does not change between upgrades.
# --no-sources: ignore server/pyproject.toml's editable path source for vitality-protocol, so
# both are copied into the venv and nothing under /opt depends on this checkout.
uv pip install --python "$APP_DIR/bin/python" --no-sources -r "$REQUIREMENTS" \
    --reinstall-package vitality-protocol --refresh-package vitality-protocol \
    --reinstall-package vitality-server --refresh-package vitality-server \
    ./protocol ./server
# Remember which checkout this came from: `vitality-server upgrade` pulls and reinstalls there.
pwd -P > "$APP_DIR/source"
chmod -R a+rX "$APP_DIR" "$PYTHON_DIR"   # the vitality user must be able to run it
# vitality-serverd is the server (run by systemd); vitality-server is the command line
# (status, doctor), put on the PATH so it works from any directory.
sudo ln -sf "$APP_DIR/bin/vitality-server" /usr/local/bin/vitality-server

echo "==> creating the vitality user and its directories"
if ! id vitality >/dev/null 2>&1; then
    sudo useradd --system --user-group --no-create-home --shell /usr/sbin/nologin vitality
fi
# The data folder is 750 vitality:vitality. Group membership lets the installing user read it
# (vitality-server status, browsing captures) without sudo; it is read-only for the group.
# (Asked of the group database, not `id`, which shows only the groups of this login.)
if ! getent group vitality | cut -d: -f4 | tr ',' '\n' | grep -qx "$(id -un)"; then
    sudo usermod -aG vitality "$(id -un)"
    echo "    added $(id -un) to the vitality group; it takes effect at your next login"
fi
sudo install -d -m 750 -o vitality -g vitality "$DATA_DIR"
sudo install -d -m 755 "$CONFIG_DIR"
if [[ -e "$CONFIG_DIR/server.toml" ]]; then
    echo "    keeping the existing $CONFIG_DIR/server.toml"
else
    sudo install -m 644 server/server.toml.example "$CONFIG_DIR/server.toml"
    echo "    wrote $CONFIG_DIR/server.toml"
fi

echo "==> installing and starting $UNIT"
sudo install -m 644 server/systemd/"$UNIT" /etc/systemd/system/"$UNIT"
sudo systemctl daemon-reload
sudo systemctl enable "$UNIT"
sudo systemctl restart "$UNIT"
STOPPED=0   # started again: a failure from here on needs no restart

if ! tailscale ip -4 >/dev/null 2>&1; then
    echo "warning: Tailscale has no address on this machine yet; the server cannot start until it does." >&2
    echo "         Run: sudo tailscale up && sudo tailscale set --hostname=vitality-librarian" >&2
fi
sleep 2
if ! systemctl is-active --quiet "$UNIT"; then
    echo "warning: $UNIT is not running; see: journalctl -u vitality-serverd -n 50" >&2
fi

cat <<NEXT

Done. Next:
  1. Edit $CONFIG_DIR/server.toml: set allowed_clients to the Tailscale names of the
     devices that may upload, then: sudo systemctl restart vitality-serverd
  2. Check it: vitality-server doctor
     and see what has arrived: vitality-server status
     (log in again once if install.sh just added you to the vitality group)
  3. From any device on the tailnet: curl http://vitality-librarian:8100/health
     (use the hostname and port you set in Tailscale and server.toml)

Later, to update from GitHub's main: vitality-server upgrade
NEXT
