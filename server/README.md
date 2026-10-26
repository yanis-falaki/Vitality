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

2. Clone the repo on the VM and install and start the service:

   ```sh
   ssh <vm>.exe.xyz
   git clone https://github.com/yanis-falaki/Vitality.git
   cd Vitality && ./server/install.sh
   ```

   The script installs Python 3.14 with `uv` and the server, with its dependencies at the
   exact versions (and hashes) in `server/uv.lock`, into `/opt/vitality-server`, creates the
   `vitality` system user, `/srv/vitality` and `/etc/vitality/server.toml`, installs the
   systemd unit, and starts it. It never overwrites an existing `server.toml`. It also
   records which checkout it was run from (`/opt/vitality-server/source`), which is how
   `vitality-server upgrade` finds the repo later. Rerunning it reinstalls the code: it
   stops the service while the program is replaced and starts it again.

   To upgrade later, run `vitality-server upgrade` on the VM (see Commands). To install code
   that is not pushed to GitHub, copy the repo over instead (`git archive HEAD | ssh
   <vm>.exe.xyz 'mkdir -p Vitality && tar -x -C Vitality'`) and run `install.sh` there;
   `upgrade` then has nothing to pull from.

3. Say which devices may upload. On the VM, edit `/etc/vitality/server.toml` and put the
   Tailscale device names in `allowed_clients` (the laptop is named `laptop`), then restart:

   ```sh
   sudo nano /etc/vitality/server.toml
   sudo systemctl restart vitality-serverd
   ```

   An empty list refuses every upload (and the server logs a warning at startup).

   The name is the first part of the device's MagicDNS name (`laptop` for
   `laptop.tail1234.ts.net`), and only devices on the VM's own tailnet count: a device
   shared in from another tailnet is refused even if its name matches.

4. Point the laptop at it: see "Upload to a server" in [`client/README.md`](../client/README.md).

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
| `vitality-server upgrade` | pulls the latest `main` from GitHub, reinstalls the server, then runs `doctor` |

All three `vitality-server` commands take `--config PATH` (default
`/etc/vitality/server.toml`). `install.sh` links `vitality-server` into `/usr/local/bin`.
`status` and `doctor` only read: they never change the data folder or the service. `status` needs no `sudo` for members of the `vitality` group,
which `install.sh` makes the installing user (log in again once for that to take effect);
`doctor` needs no group at all.

`upgrade` is for later updates. Run it as your normal user, not with `sudo` (git and
`install.sh` use your account; `install.sh` uses `sudo` itself). It pulls from GitHub's
`main`, so **what you push to `main` is what the next upgrade installs**. In four steps, each
printed under a `==>` header:

1. finds the checkout `install.sh` was last run from (`/opt/vitality-server/source`);
2. fetches `origin/main` and fast-forwards the checkout to it. It refuses, installing
   nothing, if the checkout is not on `main`, has modified tracked files, or has diverged
   from `origin/main`. With nothing new it says `already up to date (<commit>)` and stops;
3. runs `./server/install.sh` there (the service is stopped while the program is replaced);
4. runs `doctor` from the newly installed program. `upgrade` exits with its exit code.

```
$ vitality-server upgrade
==> finding the checkout
    /home/exedev/Vitality
==> pulling main
2 new commit(s):
  3f2a1bc fix upload retry
  9d41e07 add the upgrade command
==> installing
...                                   (install.sh's own output)
==> checking
✓ config loads
...                                   (doctor's output)
```

```
$ vitality-server status
laptop: 595 interactions · 1835 shots · 201.0 MB · last received 17:30:37 EDT
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
