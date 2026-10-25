"""What both programs use: `vitality-serverd` (daemon/) and `vitality-server` (cli/).

config.py     the server's settings file (server.toml)
identity/     which device sent a request, asked of Tailscale (`Identity`)
storage/      saving uploaded interactions on disk, with a SQLite index (`InteractionStore`),
              and reading that index back read-only (`read_summary`)
"""
