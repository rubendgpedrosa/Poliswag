#!/usr/bin/env python3
"""Alert the owner on his phone through Home Assistant, without the bot process.

For host scripts that must report when Poliswag itself can't: the watchdog
restarting it, a failed database backup. POSTs to OWNER_ALERT_ENDPOINT (the HA
legacy host-alert webhook) from /root/Poliswag/.env; if that is
unset or HA doesn't answer, falls back to a Discord DM (dm_owner.py).
Standard library only, so it runs on the bare host.

    scripts/notify_owner.py [--discord] "message text" ["Title"]

Use --discord for Pokémon GO alerts; generic host alerts retain the HA route.
"""

import argparse
import json
import re
import sys
import urllib.request

if __package__:
    from . import dm_owner
else:
    import dm_owner


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--discord", action="store_true")
    parser.add_argument("message")
    parser.add_argument("title", nargs="?", default="Poliswag")
    args = parser.parse_args()
    message, title = args.message, args.title
    if args.discord:
        sys.argv = [sys.argv[0], message, title]
        dm_owner.main()
        return
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
