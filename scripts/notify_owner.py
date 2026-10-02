#!/usr/bin/env python3
"""Alert the owner on his phone through Home Assistant, without the bot process.

For host scripts that must report when Poliswag itself can't: the watchdog
restarting it, a failed database backup. POSTs to OWNER_ALERT_ENDPOINT (the HA
webhook behind modules/owner_alert.py) from /root/Poliswag/.env; if that is
unset or HA doesn't answer, falls back to a Discord DM (dm_owner.py).
Standard library only, so it runs on the bare host.

    scripts/notify_owner.py "message text" ["Title"]
"""

import json
import re
import sys
import urllib.request

import dm_owner


def main():
    if len(sys.argv) not in (2, 3):
        sys.exit('usage: notify_owner.py MESSAGE ["TITLE"]')
    message = sys.argv[1]
    title = sys.argv[2] if len(sys.argv) == 3 else "Poliswag"
    endpoint = dm_owner.read_env(dm_owner.ENV_FILE).get("OWNER_ALERT_ENDPOINT")
    if endpoint:
        body = {
            "title": title,
            # Discord markdown means nothing in a phone notification.
            "message": re.sub(r"\*\*|`", "", message),
            "tag": "poliswag-host",
        }
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=10):
                return
        except OSError as error:
            print(
                f"Home Assistant unreachable ({error}); sending a DM", file=sys.stderr
            )
    sys.argv = [sys.argv[0], message, title]
    dm_owner.main()


if __name__ == "__main__":
    main()
