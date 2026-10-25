"""The Vitality server: receives interactions uploaded by clients and stores them.

app.py        `create_app(store, identity, allowed_clients)`: builds the app from the parts below;
              the route table and status codes are listed at its top
routes/       one module per group of routes (`interactions`, `health`), each a `router`
gate.py       `UploadGate`: the size and sending-device checks, before the body is parsed
errors.py     the `ErrorReply` helper and the handlers that turn exceptions into one
config.py     the server's settings file (server.toml)
identity/     which device sent a request, asked of Tailscale (`Identity`)
storage/      saving uploaded interactions on disk, with a SQLite index (`InteractionStore`),
              and reading that index back read-only (`read_summary`)
cli/          the `vitality-server` command line (`status`, `doctor`): looks at the server and
              its data without changing anything
main.py       the `vitality-serverd` entry point (the server itself): load the settings, open
              the store, find the Tailscale address, serve the app there with uvicorn

The routes and messages it shares with the client are in `vitality_protocol`.
"""
