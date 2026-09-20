"""Announce newly connected trade lists in the community trade channel.

The worker deliberately owns only trade_notice and the polling watermark. The
web app remains the sole writer of trade_entry.
"""

import asyncio
from collections import defaultdict

import discord
import pymysql

from modules.config import Config

_CUTOFF_SQL = "UTC_TIMESTAMP() - INTERVAL 90 SECOND"
_DETAIL_LIMIT = 8
_MENTION_LIMIT = 20
TRADES_CHANNEL_ID = 799442640910549022


class TradeAnnouncer:
    def __init__(self, poliswag):
        self.poliswag = poliswag
        self._disabled_logged = False

    async def tick(self):
        if not Config.TRADES_CHANNEL_ID:
            if not self._disabled_logged:
                self.poliswag.utility.log_to_file(
                    "TRADES_CHANNEL_ID is unset; trade announcements disabled"
                )
                self._disabled_logged = True
            return
        self._disabled_logged = False
        channel = self.poliswag.get_channel(Config.TRADES_CHANNEL_ID)
        if channel is None:
            channel = await self.poliswag.fetch_channel(Config.TRADES_CHANNEL_ID)

        batch = await asyncio.to_thread(self._read_batch)
        if batch["initialise"]:
            await asyncio.to_thread(self._initialise, batch["cutoff"])
            return
        if not batch["connections"]:
            await asyncio.to_thread(self._advance, batch["cutoff"])
            return

        grouped = defaultdict(list)
        for row in batch["connections"]:
            grouped[str(row["actor_id"])].append(row)
        covered = []
        for actor_id, rows in grouped.items():
            content, embed, mentions, _collapsed = self._message(actor_id, rows)
            await channel.send(
                content=content,
                embed=embed,
                allowed_mentions=discord.AllowedMentions(
                    users=[discord.Object(id=int(user_id)) for user_id in mentions],
                    roles=False,
                    everyone=False,
                    replied_user=False,
                ),
            )
            covered.extend(rows)
        await asyncio.to_thread(self._record, covered, batch["cutoff"])

    def _connect(self):
        return pymysql.connect(
            host=Config.DB_HOST,
            port=Config.DB_PORT,
            user=Config.DB_USER,
            password=Config.DB_PASSWORD,
            database=Config.DB_POGOLEIRIA,
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=False,
        )

    def _read_batch(self):
        db = self._connect()
        try:
            with db.cursor() as cursor:
                cursor.execute("SELECT " + _CUTOFF_SQL + " AS cutoff")
                cutoff = cursor.fetchone()["cutoff"]
                cursor.execute("SELECT last_trade_notice_at FROM poliswag.poliswag")
                watermark = cursor.fetchone()["last_trade_notice_at"]
                if watermark is None:
                    return {"cutoff": cutoff, "initialise": True, "connections": []}
                cursor.execute(
                    """
                    SELECT DISTINCT h.discord_id AS holder_id, w.discord_id AS wanter_id,
                           h.pokemon_id, h.form_id, h.category,
                           h.created_at AS have_created_at, w.created_at AS want_created_at,
                           CASE WHEN h.created_at >= w.created_at THEN h.discord_id ELSE w.discord_id END actor_id,
                           hp.username AS holder_username, hp.display_name AS holder_display_name,
                           wp.username AS wanter_username, wp.display_name AS wanter_display_name,
                           pn.name AS pokemon_name, pn.form_name
                    FROM trade_entry h
                    JOIN trade_entry w ON w.list = 'want'
                      AND (w.pokemon_id = 0 OR w.pokemon_id = h.pokemon_id)
                      AND (w.form_id = 0 OR w.form_id = h.form_id)
                      AND (w.category = 'normal' OR w.category = h.category)
                      AND w.discord_id <> h.discord_id
                    JOIN trade_player hp ON hp.discord_id = h.discord_id AND hp.left_at IS NULL
                    JOIN trade_player wp ON wp.discord_id = w.discord_id AND wp.left_at IS NULL
                    LEFT JOIN poliswag.pokemon_name pn ON pn.pokemon_id = h.pokemon_id AND pn.form_id = h.form_id
                    LEFT JOIN trade_notice n ON n.wanter_id = w.discord_id AND n.holder_id = h.discord_id
                      AND n.pokemon_id = h.pokemon_id AND n.form_id = h.form_id AND n.category = h.category
                    WHERE h.list = 'have' AND h.created_at <= %s AND w.created_at <= %s
                      AND GREATEST(h.created_at, w.created_at) > %s
                      AND n.wanter_id IS NULL
                    ORDER BY GREATEST(h.created_at, w.created_at), w.discord_id,
                             h.discord_id, h.pokemon_id, h.form_id, h.category
                    """,
                    (cutoff, cutoff, watermark),
                )
                return {
                    "cutoff": cutoff,
                    "initialise": False,
                    "connections": list(cursor.fetchall()),
                }
        finally:
            db.rollback()
            db.close()

    def _initialise(self, cutoff):
        db = self._connect()
        try:
            with db.cursor() as cursor:
                cursor.execute(
                    "UPDATE poliswag.poliswag SET last_trade_notice_at = %s", (cutoff,)
                )
            db.commit()
        finally:
            db.close()

    def _advance(self, cutoff):
        self._record([], cutoff)

    def _record(self, rows, cutoff):
        db = self._connect()
        try:
            with db.cursor() as cursor:
                for row in rows:
                    cursor.execute(
                        "INSERT IGNORE INTO trade_notice (wanter_id, holder_id, pokemon_id, form_id, category) VALUES (%s,%s,%s,%s,%s)",
                        (
                            row["wanter_id"],
                            row["holder_id"],
                            row["pokemon_id"],
                            row["form_id"],
                            row["category"],
                        ),
                    )
                cursor.execute(
                    "UPDATE poliswag.poliswag SET last_trade_notice_at = %s", (cutoff,)
                )
            db.commit()
        finally:
            db.close()

    def _message(self, actor_id, rows):
        actor = next(row for row in rows if str(row["actor_id"]) == str(actor_id))
        mentions = {str(actor_id)}
        lines = []
        for row in rows[:_DETAIL_LIMIT]:
            mentions.add(str(row["wanter_id"]))
            pokemon = row["pokemon_name"] or f"#{row['pokemon_id']}"
            if row["form_name"]:
                pokemon += f" ({row['form_name']})"
            direction = (
                "procura" if str(row["actor_id"]) == str(row["holder_id"]) else "têm"
            )
            lines.append(
                f"{pokemon} — {direction}: <@{row['wanter_id'] if direction == 'procura' else row['holder_id']}>"
            )
        if len(mentions) > _MENTION_LIMIT or len(rows) > _DETAIL_LIMIT:
            embed = discord.Embed(
                description=f"{actor['display_name']} atualizou as listas; {len(rows)} pessoas encontram algo nelas",
                color=Config.EMBED_COLOR,
            )
            return None, embed, set(), True
        embed = discord.Embed(description="\n".join(lines), color=Config.EMBED_COLOR)
        embed.title = f"🔄 TRADES — <@{actor_id}> atualizou as listas"
        embed.url = f"{Config.TRADES_URL}/possiveis"
        content = f"<#{TRADES_CHANNEL_ID}> " + " ".join(
            f"<@{user_id}>" for user_id in sorted(mentions)
        )
        return content, embed, mentions, False
