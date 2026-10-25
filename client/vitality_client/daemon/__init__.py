"""The daemon (`vitality-clientd`): records interactions — screenshots around user activity.

daemon.py   the contract (`Daemon`, `Status`) at the top, `CaptureDaemon` below — it wires
            the parts together: activity → policy → frame → storage → upload, plus the
            control socket
main.py     the `vitality-clientd` entry point: picks the concrete parts and runs it

The parts it alone uses live here too: policy/ (when to capture), activity/ (input timing),
frames/ (screen images), storage/ (saving and indexing), upload/ (sending to the server).
"""
