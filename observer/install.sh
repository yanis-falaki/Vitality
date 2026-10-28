#!/usr/bin/env bash
# Install or upgrade the Vitality observer on this machine (the VM that runs the server).
#
# Run it as your normal user (it uses sudo where it must), from the root of the repo,
# after ./server/install.sh (the observer reads the captures the server stores):
#   ./observer/install.sh
# Rerunning is safe: it stops the service, upgrades the code, keeps /etc/vitality/observer.toml
# and the user model, and starts the service again.
set -euo pipefail

APP_DIR=/opt/vitality-observer          # the observer's virtual environment
PYTHON_DIR=/opt/vitality-python         # uv's Python, shared with the server, readable by every user
ACCOUNT=vitality-librarian              # the account the observer, and the Claude Code it starts, run as
HOME_DIR=/var/lib/vitality-librarian    # its home: Claude Code's login, skipped.txt, calls.jsonl
MODEL_DIR=/srv/vitality-model           # the user model, a git repository
CONFIG_DIR=/etc/vitality
UNIT=vitality-observerd.service
# Where the unit looks for `claude`: the account's own copy first (see the end of this script).
CLAUDE_PATH="$HOME_DIR/.local/bin:/usr/local/bin:/usr/bin:/bin"

if [[ ! -f observer/pyproject.toml ]]; then
    echo "usage: run ./observer/install.sh from the root of the Vitality repo (the directory with observer/)" >&2
    exit 1
fi
if ! getent group vitality >/dev/null; then
    echo "there is no vitality group: run ./server/install.sh first (the observer reads the captures it stores)" >&2
    exit 1
fi
command -v git >/dev/null 2>&1 || { echo "git is needed for the user model; install it first" >&2; exit 1; }

# uv: use the one on the machine, else install it for this user.
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
    echo "==> installing uv"
    curl -LsSf https://astral.sh/uv/install.sh | sh
fi
command -v uv >/dev/null 2>&1 || { echo "uv is not on PATH after installing it" >&2; exit 1; }

# On exit: delete the temporary requirements file, and if the script stopped the service and
# then failed, start it again so a failed install does not leave the observer down.
REQUIREMENTS=""
STOPPED=0
cleanup() {
    local status=$?
    [[ -z "$REQUIREMENTS" ]] || rm -f "$REQUIREMENTS"
    if [[ $status -ne 0 && $STOPPED -eq 1 ]]; then
        sudo systemctl start "$UNIT" || true
        echo "install failed; restarted the previous vitality-observerd (it may be running partly replaced files: rerun ./observer/install.sh)" >&2
    fi
}
trap cleanup EXIT

# Stop the running observer before its files are replaced (the first install has no unit yet).
if systemctl is-active --quiet "$UNIT"; then
    echo "==> stopping vitality-observerd while the program is replaced"
    STOPPED=1   # set first: if the stop half-works, a failure still tries to start it
    sudo systemctl stop "$UNIT"
fi

echo "==> installing Python 3.14 and the observer into $APP_DIR"
export UV_PYTHON_INSTALL_DIR="$PYTHON_DIR"
sudo install -d -m 755 -o "$(id -un)" -g "$(id -gn)" "$APP_DIR" "$PYTHON_DIR"
uv python install 3.14
[[ -x "$APP_DIR/bin/python" ]] || uv venv --python 3.14 "$APP_DIR"
# The third-party dependencies at exactly the versions in observer/uv.lock, with their hashes
# (uv checks every hash given). --locked stops here if the lock is out of date with
# observer/pyproject.toml. The observer itself is left out of the export (it has no hash) and
# is installed from this checkout below.
REQUIREMENTS=$(mktemp)
(cd observer && uv export --locked --no-dev --no-editable --no-emit-project \
    --quiet) > "$REQUIREMENTS"
# Reinstall the observer every time: the version number does not change between upgrades.
# --no-sources: copy it into the venv, so nothing under /opt depends on this checkout.
uv pip install --python "$APP_DIR/bin/python" --no-sources -r "$REQUIREMENTS" \
    --reinstall-package vitality-observer --refresh-package vitality-observer \
    ./observer
chmod -R a+rX "$APP_DIR" "$PYTHON_DIR"   # the librarian account must be able to run it
sudo ln -sf "$APP_DIR/bin/vitality-observerd" /usr/local/bin/vitality-observerd

echo "==> creating the $ACCOUNT account and the user model"
if ! id "$ACCOUNT" >/dev/null 2>&1; then
    # Its home holds Claude Code's login, so it needs one; nobody logs in as it (nologin):
    # it is used only through `sudo -u`, for the one-time Claude Code install and login.
    sudo useradd --system --user-group --home-dir "$HOME_DIR" --create-home --shell /usr/sbin/nologin "$ACCOUNT"
fi
# Group vitality is what lets the account read the captures (the data folder is 750
# vitality:vitality), and read only: it does not own them. Group membership is looked up in
# the group database, not with `id`, which shows only the groups of the current login.
if ! getent group vitality | cut -d: -f4 | tr ',' '\n' | grep -qx "$ACCOUNT"; then
    sudo usermod -aG vitality "$ACCOUNT"
    echo "    added $ACCOUNT to the vitality group"
fi
# Group $ACCOUNT lets the installing user read the user model, which is 750 and its own.
if ! getent group "$ACCOUNT" | cut -d: -f4 | tr ',' '\n' | grep -qx "$(id -un)"; then
    sudo usermod -aG "$ACCOUNT" "$(id -un)"
    echo "    added $(id -un) to the $ACCOUNT group; it takes effect at your next login"
fi
sudo install -d -m 750 -o "$ACCOUNT" -g "$ACCOUNT" "$MODEL_DIR"
# The model is a git repository so its history is kept. Git is run as the account, so the
# files it creates are the account's. The identity is for commits made later.
if [[ ! -d "$MODEL_DIR/.git" ]]; then
    sudo -u "$ACCOUNT" -H git -C "$MODEL_DIR" init
    echo "    initialised $MODEL_DIR as a git repository"
fi
sudo -u "$ACCOUNT" -H git -C "$MODEL_DIR" config user.name "Vitality observer"
sudo -u "$ACCOUNT" -H git -C "$MODEL_DIR" config user.email "observer@vitality.local"

sudo install -d -m 755 "$CONFIG_DIR"
if [[ -e "$CONFIG_DIR/observer.toml" ]]; then
    echo "    keeping the existing $CONFIG_DIR/observer.toml"
else
    sudo install -m 644 observer/observer.toml.example "$CONFIG_DIR/observer.toml"
    echo "    wrote $CONFIG_DIR/observer.toml"
fi

# Claude Code is not installed by this script: it belongs to the $ACCOUNT account (Anthropic's
# installer puts it in that account's home, where it also updates itself), and logging in
# needs a browser. Find out which of the two one-time steps, if any, is still to do.
CLAUDE_INSTALL="sudo -u $ACCOUNT -H bash -c 'curl -fsSL https://claude.ai/install.sh | bash'"
CLAUDE_LOGIN="sudo -u $ACCOUNT -H $HOME_DIR/.local/bin/claude"
CLAUDE_READY=0
if ! sudo -u "$ACCOUNT" -H env PATH="$CLAUDE_PATH" claude --version >/dev/null 2>&1; then
    CLAUDE_STEPS=$(cat <<STEPS
  1. Install Claude Code for the $ACCOUNT account (once):
       $CLAUDE_INSTALL
     then log it in to your Claude subscription (once): run
       $CLAUDE_LOGIN
     type /login, follow the link, then exit with /exit.
STEPS
)
# The login is a file in the account's home, which this user cannot read, hence sudo.
elif ! sudo test -f "$HOME_DIR/.claude/.credentials.json"; then
    CLAUDE_STEPS=$(cat <<STEPS
  1. Log Claude Code in to your Claude subscription (once): run
       $CLAUDE_LOGIN
     type /login, follow the link, then exit with /exit.
STEPS
)
else
    CLAUDE_READY=1
    CLAUDE_STEPS="  1. Claude Code is installed and logged in for $ACCOUNT: nothing to do."
fi

echo "==> installing $UNIT"
sudo install -m 644 observer/systemd/"$UNIT" /etc/systemd/system/"$UNIT"
sudo systemctl daemon-reload
sudo systemctl enable "$UNIT"
# Start it only once Claude Code is ready (or if it was running before this upgrade): started
# earlier, every call would fail, and the observer gives up on interactions that keep failing.
if [[ $CLAUDE_READY -eq 1 || $STOPPED -eq 1 ]]; then
    sudo systemctl restart "$UNIT"
    STOPPED=0   # started again: a failure from here on needs no restart
    sleep 2
    if ! systemctl is-active --quiet "$UNIT"; then
        echo "warning: $UNIT is not running; see: journalctl -u vitality-observerd -n 50" >&2
    fi
    START_STEP="  2. Watch it work:"
else
    START_STEP="  2. Once that is done, start it and watch it work:
       sudo systemctl start vitality-observerd"
fi

cat <<NEXT

Done. Next:
$CLAUDE_STEPS
$START_STEP
       journalctl -u vitality-observerd -f
  3. Observations appear as $MODEL_DIR/observations/<device>/<day>/<time>.md,
     one per interaction (log in again once if install.sh just added you to the
     $ACCOUNT group, to read them without sudo). Cost and duration of each Claude
     call: $HOME_DIR/calls.jsonl

Settings: $CONFIG_DIR/observer.toml (then: sudo systemctl restart vitality-observerd)
To update: pull the repo and run ./observer/install.sh again.
NEXT
