"""The Vitality server: receives interactions uploaded by clients and stores them.

daemon/   the server itself (`vitality-serverd`): the web app, its checks, its routes, and
          the entry point that serves it on the Tailscale address
cli/      the `vitality-server` command line (`status`, `doctor`): looks at the server and
          its data without changing anything
shared/   what both programs use: the settings file, interaction storage, device identity

`daemon/` and `cli/` never import each other; both may import `shared/`; `shared/` imports
neither. The routes and messages the server shares with the client are in `vitality_protocol`.
"""
