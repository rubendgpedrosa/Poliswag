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
# A longer list is a wall nobody reads anyway.
_LINE_LIMIT = 12
# One player adding forty Pokémon used to fill every line and push everyone
# else into "… e mais". Past this many, theirs collapse into one line.
_PER_PLAYER = 3
# A field holds 1024 characters. Each line now carries a profile link, so
# twelve of them can pass that; lines stop here and the rest are counted.
_FIELD_CHARS = 1024
# From this few entries down, the Pokémon is the post's picture rather than a
# corner thumbnail: a one-line post still gets looked at.
_BIG_ART_UP_TO = 2

# The icon set the map, the hub and the trades app all draw with, so a Pokémon
# looks the same in Discord as it does on the site.
SPRITE_BASE = "https://raw.githubusercontent.com/nileplumb/PkmnHomeIcons/master/UICONS_OS_128/pokemon"
# The same files at 512px, for the big picture.
LARGE_SPRITE_BASE = (
    "https://raw.githubusercontent.com/nileplumb/PkmnHomeIcons/master/UICONS_OS/pokemon"
)

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


# `matched`: someone else, still in the server, already has what this want
# asks for, or already wants what this have offers. Same rule as the
# announcer's, which posts those pairs as they connect.
_ROWS_SQL = """
    SELECT e.discord_id, e.list, e.category, e.pokemon_id, e.form_id,
           COALESCE(p.trainer_name, p.display_name) AS display_name,
           pn.name AS pokemon_name, pn.form_name,
           (e.list = 'have' AND EXISTS (
              SELECT 1 FROM trade_entry w
              JOIN trade_player wp ON wp.discord_id = w.discord_id AND wp.left_at IS NULL
              WHERE w.list = 'want' AND w.discord_id <> e.discord_id
                AND (w.pokemon_id = 0 OR w.pokemon_id = e.pokemon_id)
                AND (w.form_id = 0 OR w.form_id = e.form_id)
                AND (w.category = 'normal' OR w.category = e.category))
           OR e.list = 'want' AND EXISTS (
              SELECT 1 FROM trade_entry h
              JOIN trade_player hp ON hp.discord_id = h.discord_id AND hp.left_at IS NULL
              WHERE h.list = 'have' AND h.discord_id <> e.discord_id
                AND (e.pokemon_id = 0 OR h.pokemon_id = e.pokemon_id)
                AND (e.form_id = 0 OR h.form_id = e.form_id)
                AND (e.category = 'normal' OR h.category = e.category))) AS matched
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


def sprite_url(row, base=SPRITE_BASE):
    form = row.get("form_id") or 0
    return f"{base}/{row['pokemon_id']}{f'_f{form}' if form else ''}.png"


# The site's `?o=` origin tag: Discord's in-app browser sends no referrer, so
# without it every visit from this post reads as "direct" in the stats.
ORIGIN_TAG = "o=trocas-resumo"


def profile_url(discord_id):
    return f"{Config.TRADES_URL.rstrip('/')}/jogador/{discord_id}?{ORIGIN_TAG}"


def _player(row):
    """The name after the dash, linked to their profile: who to talk to, one
    tap away. Brackets would end the link text early."""
    name = discord.utils.escape_markdown(str(row["display_name"]))
    name = name.replace("[", "(").replace("]", ")")
    return f"[{name}]({profile_url(row['discord_id'])})"


def _line(row):
    mark = " 🤝" if row.get("matched") else ""
    return f"{describe(row)}{mark} — {_player(row)}"


def _field(rows):
    # (text, how many entries it stands for), so a line dropped for length
    # is still counted in "… e mais".
    lines, collapsed, left_out = [], {}, 0
    per_player = {}
    for row in rows:
        seen = per_player.get(row["discord_id"], 0)
        per_player[row["discord_id"]] = seen + 1
        if seen >= _PER_PLAYER:
            collapsed.setdefault(row["discord_id"], [row, 0])[1] += 1
        elif len(lines) < _LINE_LIMIT:
            lines.append((_line(row), 1))
        else:
            left_out += 1
    lines += [
        (f"*+{count} de {_player(row)}*", count) for row, count in collapsed.values()
    ]

    # Keep room for the "… e mais" line itself.
    while lines and len("\n".join(text for text, _ in lines)) > _FIELD_CHARS - 40:
        left_out += lines.pop()[1]
    texts = [text for text, _ in lines]
    if left_out:
        texts.append(f"*… e mais {left_out}*")
    return "\n".join(texts)


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
    lists_url += f"?{ORIGIN_TAG}"
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
    if pictured and len(rows) <= _BIG_ART_UP_TO:
        embed.set_image(url=sprite_url(pictured, LARGE_SPRITE_BASE))
    elif pictured:
        embed.set_thumbnail(url=sprite_url(pictured))

    count = f"{len(rows)} novidades" if len(rows) != 1 else "1 novidade"
    legend = " · 🤝 já tem par" if any(row.get("matched") for row in rows) else ""
    embed.set_footer(text=f"{count}{legend} · escreve !trades para entrares")
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
