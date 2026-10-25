"""The Vitality server: receives interactions uploaded by clients and stores them.

main.py       the `vitality-server` entry point: load the settings, open the store, find the
              Tailscale address, serve the app there with uvicorn
app.py        the HTTP routes (`create_app(store, identity, allowed_clients)`): size checks,
              the sending device, then storing; the status codes are listed at its top
config.py     the server's settings file (server.toml)
identity/     which device sent a request, asked of Tailscale (`Identity`)
storage/      saving uploaded interactions on disk, with a SQLite index (`InteractionStore`)

The routes and messages it shares with the client are in `vitality_protocol`.
"""
