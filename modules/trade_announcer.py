"""Announce newly connected trade lists in the community trade channel.

The worker deliberately owns only trade_notice and the polling watermark. The
web app remains the sole writer of trade_entry.
"""

import asyncio
from collections import defaultdict

import discord
import pymysql

from modules.config import Config
from modules.trade_digest import CATEGORY_LABELS

_CUTOFF_SQL = "UTC_TIMESTAMP() - INTERVAL 90 SECOND"
_DETAIL_LIMIT = 8


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
            # Record each group the moment it lands. Recording the whole batch
            # at the end meant one failing group re-announced every group
            # before it on the next tick, and every 60s after that.
            await asyncio.to_thread(self._record_notices, rows)
        # Only once the whole batch is out: the watermark is a time bound, and
        # moving it past a group that never got sent would drop it for good.
        await asyncio.to_thread(self._advance_watermark, batch["cutoff"])

    def _connect(self):
        return pymysql.connect(
            host=Config.DB_HOST,
            port=Config.DB_PORT,
            user=Config.DB_USER,
            password=Config.DB_PASSWORD,
            database=Config.DB_POGOLEIRIA,
            # Without these a stalled read blocks the 60s tick for good, and
            # every later step with it -- including _check_workers, which feeds
            # StackRecovery's self-healing.
            connect_timeout=3,
            read_timeout=5,
            write_timeout=3,
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
                # No settings row at all reads the same as no watermark: set
                # one and announce nothing, rather than raising every tick.
                settings = cursor.fetchone()
                watermark = settings["last_trade_notice_at"] if settings else None
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
        self._advance_watermark(cutoff)

    def _record_notices(self, rows):
        """Mark these connections as announced, so they are never sent twice."""
        if not rows:
            return
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
            db.commit()
        finally:
            db.close()

    def _advance_watermark(self, cutoff):
        db = self._connect()
        try:
            with db.cursor() as cursor:
                cursor.execute(
                    "UPDATE poliswag.poliswag SET last_trade_notice_at = %s", (cutoff,)
                )
            db.commit()
        finally:
            db.close()

    def _message(self, actor_id, rows):
        actor = next(row for row in rows if str(row["actor_id"]) == str(actor_id))
        # The actor is whichever side posted last, so their name is under the
        # holder or the wanter column. There is no bare display_name.
        actor_name = (
            actor["holder_display_name"]
            if str(actor["actor_id"]) == str(actor["holder_id"])
            else actor["wanter_display_name"]
        )
        mentions = {str(actor_id)}
        lines = []
        for row in rows[:_DETAIL_LIMIT]:
            mentions.add(str(row["wanter_id"]))
            pokemon = row["pokemon_name"] or f"#{row['pokemon_id']}"
            if row["form_name"]:
                pokemon += f" ({row['form_name']})"
            # "Pikachu · Shiny", as the site and the morning digest write it:
            # a Shiny and a plain Pikachu are different trades.
            label = CATEGORY_LABELS.get(row["category"])
            if label:
                pokemon += f" · {label}"
            direction = (
                "procura" if str(row["actor_id"]) == str(row["holder_id"]) else "tem"
            )
            lines.append(
                f"{pokemon} — {direction}: <@{row['wanter_id'] if direction == 'procura' else row['holder_id']}>"
            )
        # Only rows[:_DETAIL_LIMIT] contribute mentions, so the row count is
        # the only thing that can overflow a message.
        if len(rows) > _DETAIL_LIMIT:
            embed = discord.Embed(
                description=f"{actor_name} atualizou as listas; {len(rows)} pessoas encontram algo nelas",
                color=Config.EMBED_COLOR,
            )
            return None, embed, set(), True
        embed = discord.Embed(description="\n".join(lines), color=Config.EMBED_COLOR)
        # A name, not <@id>: Discord doesn't render mentions in an embed
        # title, so the title showed the raw id. The mention is in content.
        embed.title = f"🔄 TRADES — {actor_name} atualizou as listas"
        # Combinações became part of Comunidade (/procurar).
        embed.url = f"{Config.TRADES_URL}/procurar"
        content = f"<#{Config.TRADES_CHANNEL_ID}> " + " ".join(
            f"<@{user_id}>" for user_id in sorted(mentions)
        )
        return content, embed, mentions, False
