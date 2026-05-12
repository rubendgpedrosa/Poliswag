#!/usr/bin/env python3
"""Refresh mock_data/ JSON files with timestamps relative to now.

Run via: python mock_data/refresh.py
Or:      make mock-data
"""

import json
import time
from pathlib import Path

ROOT = Path(__file__).parent
NOW = int(time.time())
NOW_MS = int(time.time() * 1000)  # milliseconds, used by device_status


def write(name: str, data: object) -> None:
    path = ROOT / name
    path.write_text(json.dumps(data, indent=2) + "\n")
    print(f"  wrote {path}")


def refresh_scanner_status() -> None:
    data = {
        "areas": [
            {
                "name": "LeiriaBigger",
                "worker_managers": [
                    {
                        "expected_workers": 3,
                        "workers": [
                            {
                                "worker_id": f"leiria-worker-{i}",
                                "last_data": NOW,
                                "connection_status": "Executing Worker",
                            }
                            for i in range(1, 4)
                        ],
                    }
                ],
            },
            {
                "name": "MarinhaGrande",
                "worker_managers": [
                    {
                        "expected_workers": 1,
                        "workers": [
                            {
                                "worker_id": "marinha-worker-1",
                                "last_data": NOW,
                                "connection_status": "Executing Worker",
                            }
                        ],
                    }
                ],
            },
        ]
    }
    write("scanner_status.json", data)


def refresh_device_status() -> None:
    data = {
        "devices": [
            {
                "dateLastMessageReceived": NOW_MS,
                "dateLastMessageSent": NOW_MS,
                "deviceId": "PoGoLeiria",
                "init": False,
                "instanceNo": 4,
                "heartbeatCheckStatus": True,
                "isAlive": True,
                "lastMemory": {"memFree": 562348, "memMitm": 258972, "memStart": 0},
                "nextId": 115,
                "noMessagesReceived": 0,
                "noMessagesSent": 114,
                "origin": "MITM-PoGoLeiria",
                "version": 20241005,
            }
        ]
    }
    write("device_status.json", data)


def refresh_account_status() -> None:
    # account_status has no live timestamps that affect bot logic — leave as-is.
    path = ROOT / "account_status.json"
    try:
        data = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    write("account_status.json", data)


if __name__ == "__main__":
    print("Refreshing mock_data/ timestamps...")
    refresh_scanner_status()
    refresh_device_status()
    refresh_account_status()
    print("Done.")
