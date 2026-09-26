"""Durable confirmation attempts. Never automatically re-send an ambiguous DM.

Discord nonce deduplication covers only a few minutes. The journal prevents a
restart (or a second worker) from issuing another send for the same revision;
recovery reads history instead. An absent/deleted/unreadable message remains
uncertain and requires an explicit new revision from the player.
"""

import asyncio
import hashlib
from contextlib import closing
from datetime import timedelta, timezone

import discord
from discord.http import Route

from modules.config import Config
from modules.database_connector import connect


def claim(discord_id, revision, channel_id):
    nonce = hashlib.sha256(f"hundo:{discord_id}:{revision}".encode()).hexdigest()[:24]
    with closing(connect(Config.DB_POGOLEIRIA, dict_rows=True, autocommit=True)) as db:
        with db.cursor() as cursor:
            # Atomic claim, conditional on the settings still being current.
            cursor.execute(
                "INSERT IGNORE INTO hundo_confirmation (discord_id, revision, channel_id, nonce) "
                "SELECT discord_id, %s, %s, %s FROM trade_player "
                "WHERE discord_id = %s AND hundo_settings_revision = %s AND left_at IS NULL "
                "AND (hundo_dms = 0 OR FIND_IN_SET('hundo', collecting)) "
                "AND GREATEST(hundo_confirmed_revision, hundo_dm_refused_revision) < %s",
                (revision, channel_id, nonce, discord_id, revision, revision),
            )
            owned = cursor.rowcount == 1
            cursor.execute(
                "SELECT *, TIMESTAMPDIFF(SECOND, started_at, UTC_TIMESTAMP(6)) AS age "
                "FROM hundo_confirmation WHERE discord_id = %s AND revision = %s",
                (discord_id, revision),
            )
            return owned, cursor.fetchone()


def finish(discord_id, revision, status, message_id=None):
    with closing(connect(Config.DB_POGOLEIRIA, autocommit=True)) as db:
        with db.cursor() as cursor:
            cursor.execute(
                "UPDATE hundo_confirmation SET status = %s, message_id = COALESCE(%s, message_id) "
                "WHERE discord_id = %s AND revision = %s "
                "AND status IN ('sending', 'uncertain')",
                (status, message_id, discord_id, revision),
            )


async def send_once(bot, row, embed):
    """True/False = known delivered/refused; None = pending/uncertain/obsolete."""
    discord_id, revision = row["discord_id"], row["hundo_settings_revision"]
    user = bot.get_user(int(discord_id)) or await bot.fetch_user(int(discord_id))
    channel = await user.create_dm()
    owned, notice = await asyncio.to_thread(claim, discord_id, revision, channel.id)
    if notice is None:
        return None  # settings changed before the claim
    if notice["status"] in ("sent", "refused"):
        return notice["status"] == "sent"
    if not owned:
        # Another worker may still be sending. Do not enable an explicit retry
        # until its bounded request has had ample time to finish.
        if notice["age"] < 120:
            return None
        try:
            after = notice["started_at"].replace(tzinfo=timezone.utc) - timedelta(
                seconds=10
            )
            async with asyncio.timeout(30):
                async for message in channel.history(
                    limit=200, after=after, oldest_first=True
                ):
                    if (
                        message.author.id == bot.user.id
                        and str(message.nonce) == notice["nonce"]
                    ):
                        await asyncio.to_thread(
                            finish, discord_id, revision, "sent", message.id
                        )
                        return True
        except (discord.HTTPException, TimeoutError):
            pass
        await asyncio.to_thread(finish, discord_id, revision, "uncertain")
        return None
    try:
        # Use the bot's authenticated/rate-limited HTTP client. Installed
        # discord.py's public send() exposes nonce but not enforce_nonce.
        async with asyncio.timeout(45):
            message = await bot.http.request(
                Route("POST", "/channels/{channel_id}/messages", channel_id=channel.id),
                json={
                    "embeds": [embed.to_dict()],
                    "allowed_mentions": {"parse": []},
                    "nonce": notice["nonce"],
                    "enforce_nonce": True,
                },
            )
    except (discord.Forbidden, discord.NotFound):
        await asyncio.to_thread(finish, discord_id, revision, "refused")
        return False
    # Other errors leave 'sending'. Even a timeout can mean Discord accepted it.
    await asyncio.to_thread(finish, discord_id, revision, "sent", message["id"])
    return True
