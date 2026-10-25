"""Sending ended interactions to the server.

Contract: `Uploader` (interface.py). The daemon builds one from its capture index, runs
`run()` in the background, calls `wake()` whenever an interaction ends, and reports
`status` in its own status reply. Implementation: `HttpUploader` (http_uploader.py).
"""

from vitality_client.daemon.upload.http_uploader import HttpUploader
from vitality_client.daemon.upload.interface import Uploader

__all__ = ["HttpUploader", "Uploader"]
