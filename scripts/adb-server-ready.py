#!/usr/bin/env python3
"""Wait for the managed ADB listener without letting a client spawn a server."""

import socket
import time

expires = time.monotonic() + 10
while True:
    try:
        with socket.create_connection(("127.0.0.1", 5037), timeout=0.5):
            break
    except OSError:
        if time.monotonic() >= expires:
            raise
        time.sleep(0.1)
