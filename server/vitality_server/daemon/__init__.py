"""The server itself (`vitality-serverd`): receives uploaded interactions and stores them.

main.py       the `vitality-serverd` entry point: load the settings, open the store, find the
              Tailscale address, serve the app there with uvicorn
app.py        `create_app(store, identity, allowed_clients)`: builds the app from the parts
              below; the route table and status codes are listed at its top
routes/       one module per group of routes (`interactions`, `health`), each a `router`
gate.py       `UploadGate`: the size and sending-device checks, before the body is parsed
errors.py     the `ErrorReply` helper and the handlers that turn exceptions into one

The parts it uses that the `vitality-server` command line uses too live in `shared/`.
"""
