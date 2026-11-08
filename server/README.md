# vitality-server

Receives the interactions that `vitality-client` uploads (screenshots plus an
`interaction.json` each) and stores them in folders you can browse and that file-based tools
can read. It is a small FastAPI app that listens **only on the VM's Tailscale address**, so
nothing outside your tailnet can reach it, and it accepts uploads only from devices listed in
`SERVER_ALLOWED_CLIENTS`. The device is identified by Tailscale, not by anything the sender says.

It runs as the `server` service of Docker Compose, started by `vitality-server serve`. To
install, run, configure, upgrade or read its logs, see the [root README](../README.md); this
page is about what the server does.

## Who may upload

`SERVER_ALLOWED_CLIENTS` in `.env` is a comma-separated list of Tailscale device names (the
laptop is named `laptop`). An empty list refuses every upload, and the server logs a warning at
startup. The name is the first part of the device's MagicDNS name (`laptop` for
`laptop.tail1234.ts.net`), and only devices on the VM's own tailnet count: a device shared in
from another tailnet is refused even if its name matches. The server asks the VM's Tailscale
who is calling through `/var/run/tailscale`, which Compose mounts into the container.

Never put a future agent or "doer" VM in the list: whatever is listed can write into this store.

To point a machine's desktop app at the server, see [`desktop/README.md`](../desktop/README.md).

## Commands

| Command | What it is |
|---|---|
| `vitality-server serve` | the server itself; Compose runs it |
| `vitality-server status` | what each device has uploaded, and free disk space |
| `vitality-server healthcheck` | asks the running server's `/health`; exit 0 if it answers. For Docker's healthcheck, not for people |

`./vitality status` runs `status` in a throwaway container. It only reads: it never changes the
data folder or the service.

```
laptop: 595 interactions · 1835 shots · 201.0 MB · last received 17:30:37
disk: 19.7 GB free
```

`status` shows the last time each device uploaded in local time (`HH:MM:SS` for today,
otherwise the date), and exits 1 if it cannot read the data folder.

## Is it working

`docker compose ps` shows the server as `healthy` once `/health` answers
`{"ok":true,"disk_free_bytes":...}`. From a device on the tailnet:

```sh
curl http://vitality-librarian:8100/health
```

The log shows the device, the interaction name and any rejection; it never contains image
contents. If the server will not start, the log says why; the usual cause is that Tailscale is
not up on the VM, since the server refuses to listen anywhere else.

While serving, the server asks Tailscale for its address every 30 s. After 3 failures in a row
(a restarted `tailscaled` replaces `/var/run/tailscale`, which the container's mount cannot
follow) or a changed address, it logs one warning saying why and exits with code 1; Docker then
restarts it with a fresh mount. One such restart after a Tailscale restart is expected.

## What is stored

In the container, `/data` is `~/vitality-data/server` on the VM:

```
tmp/                              uploads being written; cleaned at startup
captures/<client>/                <client> is the Tailscale device name, e.g. laptop
  2026-10-25/17-00-00/            the interaction, named by its UTC start time
    0000.webp                     the screen just before the first input
    0004.webp                     later shots, named by seconds since the start
    interaction.json              times, time zone and the list of shots
```

Files are **not encrypted** on the VM and nothing backs it up: the laptop keeps every capture,
so the laptop is the backup for now.

Uploads are checked (name, file list and sizes, WebP headers). A repeated upload of the same
interaction changes nothing and is answered as already stored. Uploads over 50 MB are refused.

## Development

```sh
uv run pytest
uv run pyright
```
