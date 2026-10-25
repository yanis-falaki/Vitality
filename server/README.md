# vitality-server

Receives the interactions that `vitality-client` uploads (screenshots plus an
`interaction.json` each) and stores them under `/srv/vitality`, in folders you can browse
and that file-based tools can read. It is a small FastAPI app that listens **only on the
VM's Tailscale address**, so nothing outside your tailnet can reach it, and it accepts
uploads only from devices named in `allowed_clients`. The device is identified by
Tailscale, not by anything the sender says.

It runs on an exe.dev VM (Ubuntu 24.04, with `uv` and `tailscale` preinstalled) as a
systemd service, `vitality-server`, which runs the program `vitality-serverd`.

## Setup

In the commands below, `<vm>` is the VM's exe.dev name; the SSH name is `<vm>.exe.xyz`
(for example `commode-doubled.exe.xyz`).

1. Join the VM to your tailnet and give it its name (once per VM). `tailscale up` prints
   a login link to open in a browser:

   ```sh
   ssh <vm>.exe.xyz
   sudo tailscale up
   sudo tailscale set --hostname=vitality-librarian
   exit
   ```

2. Copy the repo to the VM. Run this from the repo root on your laptop:

   ```sh
   rsync -a --exclude .venv --exclude .git --exclude .superpowers --exclude __pycache__ \
       ./ <vm>.exe.xyz:Vitality/
   ```

3. Install and start the service:

   ```sh
   ssh <vm>.exe.xyz 'cd Vitality && ./server/install.sh'
   ```

   The script installs Python 3.14 with `uv` and the server, with its dependencies at the
   exact versions (and hashes) in `server/uv.lock`, into `/opt/vitality-server`, creates the
   `vitality` system user, `/srv/vitality` and `/etc/vitality/server.toml`, installs the
   systemd unit, and starts it. It never overwrites an existing `server.toml`. Repeat
   steps 2 and 3 to upgrade: the code is reinstalled and the service restarted.

4. Say which devices may upload. On the VM, edit `/etc/vitality/server.toml` and put the
   Tailscale device names in `allowed_clients` (the laptop is named `laptop`), then restart:

   ```sh
   sudo nano /etc/vitality/server.toml
   sudo systemctl restart vitality-serverd
   ```

   An empty list refuses every upload (and the server logs a warning at startup).

   The name is the first part of the device's MagicDNS name (`laptop` for
   `laptop.tail1234.ts.net`), and only devices on the VM's own tailnet count: a device
   shared in from another tailnet is refused even if its name matches.

5. Point the laptop at it: see "Upload to a server" in [`client/README.md`](../client/README.md).

## Configuration

`/etc/vitality/server.toml` (see [`server.toml.example`](server.toml.example)):

```toml
data_dir = "/srv/vitality"       # where captures/, tmp/ and index.sqlite are kept
port = 8100                      # served on the VM's Tailscale address only
allowed_clients = ["laptop"]     # Tailscale device names allowed to upload
```

`data_dir` must stay under `/srv/vitality`: the service can write nowhere else. To move it,
also change `ReadWritePaths` in `/etc/systemd/system/vitality-serverd.service` (and
`systemctl daemon-reload`); note that `install.sh` overwrites the unit on each run.

Changes take effect after `sudo systemctl restart vitality-serverd`. To use another file,
run `vitality-serverd --config PATH`.

## Commands

| Command | What it is |
|---|---|
| `vitality-serverd` | the server itself; systemd runs it, you never need to |
| `vitality-server status` | what each device has uploaded, free disk space, where things live |
| `vitality-server doctor` | checks the setup, one line per check, and says what to do about a failure |

Both `vitality-server` commands take `--config PATH` (default `/etc/vitality/server.toml`).
`install.sh` links `vitality-server` into `/usr/local/bin`. They only read: they never change
the data folder or the service. `status` needs no `sudo` for members of the `vitality` group,
which `install.sh` makes the installing user (log in again once for that to take effect);
`doctor` needs no group at all.

```
$ vitality-server status
laptop: 595 interactions · 1835 shots · 201.0 MB · last received 21:30:37
disk: 19.7 GB free
config:   /etc/vitality/server.toml
data:     /srv/vitality
captures: /srv/vitality/captures
index:    /srv/vitality/index.sqlite
program:  /opt/vitality-server
service:  /etc/systemd/system/vitality-serverd.service
logs:     journalctl -u vitality-serverd

$ vitality-server doctor
✓ config loads
✓ tailscale up (100.101.102.103)
✓ service running
✓ /health answers on 100.101.102.103:8100
✓ data folder present
✗ allowed_clients set — edit /etc/vitality/server.toml, then sudo systemctl restart vitality-serverd
✓ disk space (19.7 GB free)
config:   /etc/vitality/server.toml
...                                   (the same paths as status)
```

`status` shows the last time each device uploaded in your local time (`HH:MM:SS` for
today, otherwise the date). `doctor` exits 1 if any check fails; `status` exits 1 if it
cannot read the data folder or the index.

## Check that it works

```sh
vitality-server doctor
systemctl status vitality-serverd
journalctl -u vitality-serverd -f        # logs: the device, the interaction name, any rejection
curl http://vitality-librarian:8100/health      # from a device on the tailnet
```

`/health` answers `{"ok":true,"disk_free_bytes":...}`. If the service will not start,
`journalctl` says why; the usual cause is that Tailscale is not up on the VM, since the
server refuses to listen anywhere else. Logs never contain image contents.

## What is stored

```
/srv/vitality/
  index.sqlite                      one row per interaction and per shot
  tmp/                              uploads being written; cleaned at startup
  captures/<client>/                <client> is the Tailscale device name, e.g. laptop
    2026-10-25/17-00-00/            the interaction, named by its UTC start time
      0000.webp                     the screen just before the first input
      0004.webp                     later shots, named by seconds since the start
      interaction.json              times, time zone and the list of shots
```

The data directory is readable by the `vitality` user and group (read-only for the group), and
`install.sh` adds you to that group, so you can browse it without `sudo` after one re-login.
Files are **not encrypted** on the VM and exe.dev does not back it up: the laptop keeps
every capture, so the laptop is the backup for now.

Never put a future agent or "doer" VM in `allowed_clients`: whatever is listed there can write
into this store.

Uploads are checked (name, file list and sizes, WebP headers). A repeated upload of the same
interaction changes nothing and is answered as already stored. Uploads over 50 MB are refused.

## Development

```sh
uv run pytest
uv run pyright
```
