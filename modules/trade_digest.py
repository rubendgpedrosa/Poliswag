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
import datetime

import discord
import pymysql

from modules.config import Config

DIGEST_HOUR = 9
# A field holds 1024 characters; twelve lines never comes close, and a longer
# list is a wall nobody reads anyway.
_LINE_LIMIT = 12

# The icon set the map, the hub and the trades app all draw with, so a Pokémon
# looks the same in Discord as it does on the site.
SPRITE_BASE = "https://raw.githubusercontent.com/nileplumb/PkmnHomeIcons/master/UICONS_OS_128/pokemon"

# The site's names for each list (apps/trades lib/lists.ts), so a post and the
# page it links to say the same thing. The announcer uses them too.
CATEGORY_LABELS = {
    "normal": None,
    "hundo": "100IV",
    "lucky": "Lucky",
    "shiny": "Shiny",
    "xxl": "XXL",
    "xxs": "XXS",
}


_ROWS_SQL = """
    SELECT e.discord_id, e.list, e.category, e.pokemon_id, e.form_id,
           COALESCE(p.trainer_name, p.display_name) AS display_name,
           pn.name AS pokemon_name, pn.form_name
    FROM trade_entry e
    JOIN trade_player p ON p.discord_id = e.discord_id AND p.left_at IS NULL
    LEFT JOIN poliswag.pokemon_name pn
      ON pn.pokemon_id = e.pokemon_id AND pn.form_id = e.form_id
    WHERE e.created_at > %s AND e.created_at <= %s
    ORDER BY e.created_at, e.discord_id, e.pokemon_id, e.form_id
"""


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
    """One entry as its own line: "Poliwag · Shiny"."""
    quality = CATEGORY_LABELS.get(row["category"])
    # pokemon_id 0 is the web app's ANY_SPECIES: a want that names only a
    # category. "Qualquer Shiny" is the whole request, not half of one.
    if not row["pokemon_id"]:
        return f"Qualquer {quality}" if quality else "Qualquer Pokémon"
    name = row["pokemon_name"] or f"#{row['pokemon_id']}"
    if row["form_name"]:
        name += f" {row['form_name']}"
    return f"{name} · {quality}" if quality else name


def sprite_url(row):
    form = row.get("form_id") or 0
    return f"{SPRITE_BASE}/{row['pokemon_id']}{f'_f{form}' if form else ''}.png"


def _field(rows):
    lines = [f"{describe(row)} — {row['display_name']}" for row in rows[:_LINE_LIMIT]]
    if len(rows) > _LINE_LIMIT:
        lines.append(f"*… e mais {len(rows) - _LINE_LIMIT}*")
    return "\n".join(lines)


def build_digest(rows):
    """One line per Pokémon, under what it is rather than under who added it.

    The first version put a player per line, with their haves and wants run
    together behind commas. People read this channel looking for a Pokémon,
    not for each other, so the two sides are now two fields and every Pokémon
    gets its own line. The name after the dash is who to talk to.
    """
    haves = [row for row in rows if row["list"] == "have"]
    wants = [row for row in rows if row["list"] == "want"]

    # The title carries the same link, but an embed title does not look like
    # one -- least of all on a phone. The address goes in the body too, written
    # out so people can see where they are going, and read it aloud to someone.
    # Comunidade, where everyone's lists are: TRADES_URL itself opens the
    # reader's own Pokédex, or the login screen.
    lists_url = f"{Config.TRADES_URL.rstrip('/')}/procurar"
    address = lists_url.split("://", 1)[-1]
    embed = discord.Embed(
        title="Novidades nas trocas",
        description=f"Vê as listas todas em **[{address}]({lists_url})**",
        color=Config.EMBED_COLOR,
        timestamp=datetime.datetime.now(),
    )
    embed.url = lists_url
    if haves:
        embed.add_field(name="✨ Para trocar", value=_field(haves), inline=False)
    if wants:
        embed.add_field(name="🔍 Procurados", value=_field(wants), inline=False)

    # "Qualquer Shiny" has no sprite of its own, so the thumbnail is the first
    # entry that names a species. A broken image is worse than no image.
    pictured = next((row for row in haves + wants if row["pokemon_id"]), None)
    if pictured:
        embed.set_thumbnail(url=sprite_url(pictured))

    embed.set_footer(
        text=(
            f"{len(rows)} novidades · escreve !trades para entrares"
            if len(rows) != 1
            else "1 novidade · escreve !trades para entrares"
        )
    )
    return embed


def _connect_pool():
    return pymysql.connect(
        host=Config.DB_HOST,
        port=Config.DB_PORT,
        user=Config.DB_USER,
        password=Config.DB_PASSWORD,
        database=Config.DB_POGOLEIRIA,
        # The digest runs inside the 60s tick; an unbounded read stalls it and
        # every step after it.
        connect_timeout=3,
        read_timeout=5,
        write_timeout=3,
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )


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
            await channel.send(
                embed=build_digest(batch["rows"]),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        await asyncio.to_thread(self._record, batch["cutoff"])

    def _connect(self):
        return _connect_pool()

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
                cursor.execute(_ROWS_SQL, (watermark, cutoff))
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


def rows_since(days):
    """The same rows the 09:00 post would use, over a window you choose.

    For !resumo, which exists because a silent digest and a broken one look
    identical from the channel. It reads only: the watermark belongs to the
    scheduled run, and a preview must not consume a morning's news.
    """
    db = _connect_pool()
    try:
        with db.cursor() as cursor:
            cursor.execute(
                "SELECT UTC_TIMESTAMP() - INTERVAL %s DAY AS since, UTC_TIMESTAMP() AS cutoff",
                (days,),
            )
            window = cursor.fetchone()
            cursor.execute(_ROWS_SQL, (window["since"], window["cutoff"]))
            return list(cursor.fetchall())
    finally:
        db.rollback()
        db.close()
