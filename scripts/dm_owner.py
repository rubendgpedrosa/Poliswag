#!/usr/bin/env python3
"""DM the owner (MY_ID) through Discord's REST API, without the bot process.

For host scripts that must report when Poliswag itself can't: the watchdog
restarting it, a failed database backup. Reads DISCORD_API_KEY and MY_ID from
/root/Poliswag/.env. Standard library only, so it runs on the bare host.

    scripts/dm_owner.py "message text"
"""

import json
import sys
import urllib.request
from pathlib import Path

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
API = "https://discord.com/api/v10"
# Discord requires a bot User-Agent in this form; Cloudflare rejects urllib's.
USER_AGENT = "DiscordBot (https://pogoleiria.pt, 1.0)"


def read_env(path):
    env = {}
    for line in path.read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep and not key.lstrip().startswith("#"):
            env[key.strip()] = value.strip().strip("\"'")
    return env


def post(path, token, body):
    request = urllib.request.Request(
        f"{API}{path}",
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Bot {token}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.load(response)


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: dm_owner.py MESSAGE")
    env = read_env(ENV_FILE)
    token, owner = env["DISCORD_API_KEY"], env["MY_ID"]
    channel = post("/users/@me/channels", token, {"recipient_id": owner})
    post(f"/channels/{channel['id']}/messages", token, {"content": sys.argv[1][:2000]})


if __name__ == "__main__":
    main()
