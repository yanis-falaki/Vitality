"""The Vitality server: receives interactions uploaded by clients and stores them.

daemon/   the server itself: the web app, its checks, its routes, and `serve`, which runs
          it on the Tailscale address
cli/      the `vitality-server` command line (`serve`, `status`, `healthcheck`): the one
          entry point; `serve` starts the daemon, the others look at it without changing
          anything
shared/   what both use: the settings (environment variables), interaction storage, device
          identity

`cli/` dispatches to `daemon/`'s `serve`, and `daemon/` never imports `cli/`; both may import
`shared/`; `shared/` imports neither. The routes and messages the server shares with the
client are in `vitality_protocol`.
"""
