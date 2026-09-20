"""One post a morning, naming what is new in the community's trade lists.

The announcer (modules/trade_announcer.py) covers *matches*: two lists that
connect, minutes after they do. This covers *supply* -- Pokémon added since
yesterday's post, whether or not anyone wants them yet -- so a list that nobody
has matched with still gets seen.

Silent when nothing was added. A digest that posts "nada de novo" every morning
teaches the channel to skip it, which costs the mornings that matter.

Nobody is pinged: this is news, not a summons. The announcer does the tagging,
because there a specific pair of people have something to do.
"""

import asyncio
from collections import OrderedDict

import discord
import pymysql

from modules.config import Config

DIGEST_HOUR = 9
_PLAYER_LIMIT = 10
_ITEM_LIMIT = 6

_QUALITY = {
    "normal": None,
    "hundo": "100%",
    "lucky": "Lucky",
    "shiny": "Shiny",
    "xxl": "XXL",
    "xxs": "XXS",
}


def is_due(now, watermark):
    """True once a day, from DIGEST_HOUR onwards.

    The watermark is the last post's timestamp, so it doubles as the "already
    posted today" flag: a restart at 10:00 sees this morning's post and stays
    quiet instead of sending a second one.
    """
    if now.hour < DIGEST_HOUR:
        return False
    if watermark is None:
        return True
    return watermark.date() < now.date()


def describe(row):
    """One entry, as a player would say it."""
    quality = _QUALITY.get(row["category"])
    # pokemon_id 0 is the web app's ANY_SPECIES: a want that names only a
    # category. "qualquer Shiny" is the whole request, not half of one.
    if not row["pokemon_id"]:
        return f"qualquer {quality}" if quality else "qualquer Pokémon"
    name = row["pokemon_name"] or f"#{row['pokemon_id']}"
    if row["form_name"]:
        name += f" {row['form_name']}"
    return f"{name} ({quality})" if quality else name


def group_rows(rows):
    """Per player, in the order they first appear: their new haves and wants."""
    groups = OrderedDict()
    for row in rows:
        key = str(row["discord_id"])
        if key not in groups:
            groups[key] = {"name": row["display_name"], "have": [], "want": []}
        groups[key][row["list"]].append(describe(row))
    return list(groups.values())


def _side(label, items):
    if not items:
        return None
    shown = ", ".join(items[:_ITEM_LIMIT])
    if len(items) > _ITEM_LIMIT:
        shown += f" (+{len(items) - _ITEM_LIMIT})"
    return f"{label} {shown}"


def build_digest(groups, total):
    lines = []
    for group in groups[:_PLAYER_LIMIT]:
        sides = [
            side
            for side in (_side("Tem:", group["have"]), _side("Procura:", group["want"]))
            if side
        ]
        lines.append(f"**{group['name']}** — " + " · ".join(sides))
    if len(groups) > _PLAYER_LIMIT:
        lines.append(f"*e mais {len(groups) - _PLAYER_LIMIT} jogadores.*")
    lines.append("")
    lines.append(
        "Ainda não tens lista? Escreve `!trades` e o Poliswag envia-te o código."
    )

    embed = discord.Embed(
        title=(
            f"🔄 TRADES — {total} novidades nas listas"
            if total != 1
            else "🔄 TRADES — uma novidade nas listas"
        ),
        description="\n".join(lines),
        color=Config.EMBED_COLOR,
    )
    embed.url = Config.TRADES_URL
    return embed


class TradeDigest:
    def __init__(self, poliswag):
        self.poliswag = poliswag
        self._disabled_logged = False

    async def tick(self, now):
        if not Config.TRADES_CHANNEL_ID:
            if not self._disabled_logged:
                self.poliswag.utility.log_to_file(
                    "TRADES_CHANNEL_ID is unset; trade digest disabled"
                )
                self._disabled_logged = True
            return
        self._disabled_logged = False

        batch = await asyncio.to_thread(self._read_batch, now)
        if batch is None:
            return
        if batch["rows"]:
            channel = self.poliswag.get_channel(Config.TRADES_CHANNEL_ID)
            if channel is None:
                channel = await self.poliswag.fetch_channel(Config.TRADES_CHANNEL_ID)
            groups = group_rows(batch["rows"])
            await channel.send(
                embed=build_digest(groups, len(batch["rows"])),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        await asyncio.to_thread(self._record, batch["cutoff"])

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

    def _read_batch(self, now):
        """Everything added since the last digest, or None if it isn't time.

        The first ever run has no watermark: it sets one and posts nothing,
        rather than announcing every list the community has ever written.
        """
        db = self._connect()
        try:
            with db.cursor() as cursor:
                cursor.execute("SELECT UTC_TIMESTAMP() AS cutoff")
                cutoff = cursor.fetchone()["cutoff"]
                cursor.execute("SELECT last_trade_digest_at FROM poliswag.poliswag")
                watermark = cursor.fetchone()["last_trade_digest_at"]
                if not is_due(now, watermark):
                    return None
                if watermark is None:
                    return {"cutoff": cutoff, "rows": []}
                cursor.execute(
                    """
                    SELECT e.discord_id, e.list, e.category, e.pokemon_id, e.form_id,
                           COALESCE(p.trainer_name, p.display_name) AS display_name,
                           pn.name AS pokemon_name, pn.form_name
                    FROM trade_entry e
                    JOIN trade_player p ON p.discord_id = e.discord_id AND p.left_at IS NULL
                    LEFT JOIN poliswag.pokemon_name pn
                      ON pn.pokemon_id = e.pokemon_id AND pn.form_id = e.form_id
                    WHERE e.created_at > %s AND e.created_at <= %s
                    ORDER BY e.created_at, e.discord_id, e.pokemon_id, e.form_id
                    """,
                    (watermark, cutoff),
                )
                return {"cutoff": cutoff, "rows": list(cursor.fetchall())}
        finally:
            db.rollback()
            db.close()

    def _record(self, cutoff):
        db = self._connect()
        try:
            with db.cursor() as cursor:
                cursor.execute(
                    "UPDATE poliswag.poliswag SET last_trade_digest_at = %s", (cutoff,)
                )
            db.commit()
        finally:
            db.close()
