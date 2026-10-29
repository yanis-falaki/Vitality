"""What both the daemon (daemon/) and the command line (cli/) use.

config.py     the server's settings, from environment variables (`ServerConfig`)
identity/     which device sent a request, asked of Tailscale (`Identity`)
storage/      saving uploaded interactions on disk, with a SQLite index (`InteractionStore`),
              and reading that index back read-only (`read_summary`)
"""
