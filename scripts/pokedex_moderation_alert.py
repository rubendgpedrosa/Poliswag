"""One moderation-channel embed per pending shiny-map access request.

Replacement for the owner-DM timer; rollout must install the updated service.
"""

import json
import os
from pathlib import Path

from modules.shiny_access_requests import request_payload, request_stamp
from scripts import dm_owner
from scripts.pokedex_validation_alert import SQL

STATE = Path("/app/logs/pokedex-moderation-alert.json")


def _save(state_path, keys):
    state_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = state_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(keys))
    os.chmod(temporary, 0o600)
    temporary.replace(state_path)


def notify_pending(rows, state_path, send, base_url):
    previous = json.loads(state_path.read_text()) if state_path.exists() else []
    current = [
        [str(row["discord_id"]), request_stamp(row["shiny_access_requested_at"])]
        for row in rows
    ]
    sent = [key for key in previous if key in current]
    count = 0
    for row, key in zip(rows, current):
        if key in sent:
            continue
        send(request_payload(row, base_url))
        sent.append(key)
        # A later failure must not cause successful messages to repeat.
        _save(state_path, sent)
        count += 1
    _save(state_path, sent)
    return count


def main():
    from modules.config import Config
    from modules.database_connector import connect

    if not Config.IS_PRODUCTION:
        raise RuntimeError("Production only")
    if not Config.DISCORD_API_KEY or not Config.MOD_CHANNEL_ID:
        raise RuntimeError("DISCORD_API_KEY and MOD_CHANNEL_ID must be configured")
    connection = connect(Config.DB_POGOLEIRIA, dict_rows=True)
    try:
        with connection.cursor() as cursor:
            cursor.execute(SQL)
            rows = cursor.fetchall()
    finally:
        connection.close()

    def send(payload):
        dm_owner.post(
            f"/channels/{Config.MOD_CHANNEL_ID}/messages",
            Config.DISCORD_API_KEY,
            payload,
        )

    count = notify_pending(rows, STATE, send, Config.POKEDEX_URL)
    if count:
        print(f"Notified moderators of {count} new request(s); {len(rows)} pending")


if __name__ == "__main__":
    main()
