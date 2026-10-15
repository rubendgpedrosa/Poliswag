"""Moderation-channel embeds for access requests and failed associations.

The existing validation-alert timer invokes this moderation-channel notifier.
"""

import json
import os
import re
import urllib.request
from pathlib import Path

from modules.shiny_access_requests import (
    failure_payload,
    request_payload,
    request_stamp,
)
from scripts import dm_owner

SQL = """SELECT discord_id, COALESCE(trainer_name, display_name) AS name,
display_name, username, avatar_url, shiny_access_requested_at FROM trade_player
WHERE left_at IS NULL AND shiny_access = 0
AND shiny_access_requested_at IS NOT NULL
ORDER BY shiny_access_requested_at, discord_id"""

STATE = Path("/app/logs/pokedex-moderation-alert.json")
FAILURE_STATE = Path("/app/logs/pokedex-link-failures.json")


def failure_sql(player_schema, link_schema):
    """Qualify both configured schemas without accepting arbitrary SQL."""
    for schema in (player_schema, link_schema):
        if not isinstance(schema, str) or not re.fullmatch(
            r"[a-zA-Z][a-zA-Z0-9_]*", schema
        ):
            raise ValueError("Invalid database schema")
    return f"""SELECT p.discord_id, COALESCE(p.trainer_name, p.display_name) AS name,
p.display_name, p.username, p.avatar_url, l.requested_at, l.resolved_at, l.fail_reason
FROM `{player_schema}`.trade_player p
JOIN `{link_schema}`.shiny_link l ON l.discord_id = p.discord_id
WHERE p.left_at IS NULL AND p.shiny_access = 1 AND l.status = 'failed'
AND l.resolved_at IS NOT NULL
ORDER BY l.resolved_at, p.discord_id"""


def _save(state_path, keys):
    state_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = state_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(keys))
    os.chmod(temporary, 0o600)
    temporary.replace(state_path)


def notify_pending(rows, state_path, send, base_url, enrich=None):
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
        send(request_payload(enrich(row) if enrich else row, base_url))
        sent.append(key)
        # A later failure must not cause successful messages to repeat.
        _save(state_path, sent)
        count += 1
    _save(state_path, sent)
    return count


def notify_failures(rows, state_path, send, base_url, enrich=None):
    previous = json.loads(state_path.read_text()) if state_path.exists() else []
    current = [
        [
            str(row["discord_id"]),
            request_stamp(row["requested_at"]),
            request_stamp(row["resolved_at"]),
        ]
        for row in rows
    ]
    sent = [key for key in previous if key in current]
    count = 0
    for row, key in zip(rows, current):
        if key in sent:
            continue
        send(failure_payload(enrich(row) if enrich else row, base_url))
        sent.append(key)
        _save(state_path, sent)
        count += 1
    _save(state_path, sent)
    return count


def discord_get(path, token):
    request = urllib.request.Request(
        f"{dm_owner.API}{path}",
        headers={"Authorization": f"Bot {token}", "User-Agent": dm_owner.USER_AGENT},
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.load(response)


def member_enricher(channel_id, token):
    """Resolve guild identity only for new requests; stored identity is fallback."""
    guild_id = None
    channel_loaded = False

    def enrich(row):
        nonlocal guild_id, channel_loaded
        try:
            if not channel_loaded:
                channel_loaded = True
                guild_id = discord_get(f"/channels/{channel_id}", token).get("guild_id")
            if not guild_id:
                return row
            member = discord_get(
                f"/guilds/{guild_id}/members/{row['discord_id']}", token
            )
        except (OSError, ValueError):
            # Missing permissions/member or a temporary Discord failure must not
            # prevent delivery. Database identity remains available.
            return row
        user = member["user"]
        discord_id = str(row["discord_id"])
        if member.get("avatar"):
            avatar = f"https://cdn.discordapp.com/guilds/{guild_id}/users/{discord_id}/avatars/{member['avatar']}.webp?size=128"
        elif user.get("avatar"):
            avatar = f"https://cdn.discordapp.com/avatars/{discord_id}/{user['avatar']}.webp?size=128"
        else:
            discriminator = int(user.get("discriminator") or 0)
            index = discriminator % 5 if discriminator else (int(discord_id) >> 22) % 6
            avatar = f"https://cdn.discordapp.com/embed/avatars/{index}.png?size=128"
        return {
            **row,
            "display_name": member.get("nick")
            or user.get("global_name")
            or user["username"],
            "username": user["username"],
            "avatar_url": avatar,
        }

    return enrich


def main():
    from modules.config import Config
    from modules.database_connector import connect

    if not Config.IS_PRODUCTION:
        raise RuntimeError("Production only")
    if not Config.DISCORD_API_KEY or not Config.MOD_CHANNEL_ID:
        raise RuntimeError("DISCORD_API_KEY and MOD_CHANNEL_ID must be configured")

    def send(payload):
        dm_owner.post(
            f"/channels/{Config.MOD_CHANNEL_ID}/messages",
            Config.DISCORD_API_KEY,
            payload,
        )

    enrich = member_enricher(Config.MOD_CHANNEL_ID, Config.DISCORD_API_KEY)
    connection = connect(Config.DB_POGOLEIRIA, dict_rows=True)
    errors = []
    try:
        for label, query, state, notify in (
            ("request(s)", SQL, STATE, notify_pending),
            (
                "failed association(s)",
                failure_sql(Config.DB_POGOLEIRIA, Config.DB_POLISWAG),
                FAILURE_STATE,
                notify_failures,
            ),
        ):
            try:
                with connection.cursor() as cursor:
                    cursor.execute(query)
                    rows = cursor.fetchall()
                count = notify(rows, state, send, Config.POKEDEX_URL, enrich=enrich)
                if count:
                    print(f"Notified moderators of {count} new {label}")
            except Exception as error:
                errors.append(error)
    finally:
        connection.close()
    if errors:
        raise ExceptionGroup("Moderation notifications failed", errors)


if __name__ == "__main__":
    main()
