"""What both programs use: `vitality-clientd` (daemon/) and `vitality-client` (cli/).

config.py    where things live (paths from XDG variables) and client.toml settings
state.py     what the daemon remembers across restarts: pause state, screen-sharing token
channel.py   how the CLI and the daemon talk: the Unix-socket protocol
             (`ControlServer` on the daemon side, `send_command` on the CLI side)
"""
