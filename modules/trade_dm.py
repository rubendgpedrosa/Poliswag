"""Tell a player, by direct message, that a new mutual trade is waiting.

When someone marks a spare (a `have` in trade_entry) that another player is
missing in a category they collect, and that player also has a spare the
first one is missing, the second player gets a DM: who, what they have for
you, what they would want from you, and a link to their page.

"Missing" is the site's derived want: a category in the player's `collecting`
with no collection_entry tick for that Pokémon and form (costumes only for
players who show them). trade_entry's typed wants are no longer written by the
site, which is why trade_announcer.py, reading only those, has gone quiet.

Only unconditional spares count (legacy "troco apenas por…" rows are left
out). The one who added the spare is not messaged: they are on the site, where
Comunidade already shows the trade in gold. Mutuals that appear without a new
spare (someone starts collecting a category) are not messaged either.

Owns trade_dm_notice (who was told about which spare) and
poliswag.last_trade_dm_at; reads trade_player.trade_dms, the player's switch.
"""

import asyncio
from collections import defaultdict

import discord
from modules.config import Config
from modules.database_connector import connect
from modules.trade_digest import CATEGORY_LABELS

# The site writes a spare and its tick in one transaction; a short delay keeps
# a spare from being read half-way through a burst of taps.
_CUTOFF_SQL = "UTC_TIMESTAMP() - INTERVAL 60 SECOND"
# Recipients per tick: discord.py sleeps out a DM rate limit inside send(),
# which would stall the whole scheduler tick. The rest wait for the next one
# (the watermark stays put until a tick finishes everyone).
_RECIPIENTS_PER_TICK = 20
# Trainers per message, and Pokémon per line.
_HOLDERS_PER_DM = 5
_ITEMS_PER_LINE = 4
ORIGIN_TAG = "o=pokedex-dm"

# A spare nobody has been told about, whose holder the recipient could give
# something back to: `r` lacks it, and has a spare `hp` lacks.
_NEW_MUTUALS_SQL = """
SELECT h.discord_id AS holder_id, r.discord_id AS recipient_id,
       h.pokemon_id, h.form_id, h.category,
       COALESCE(hp.trainer_name, hp.display_name) AS holder_name,
       pn.name AS pokemon_name, pn.form_name
FROM trade_entry h
JOIN trade_player hp ON hp.discord_id = h.discord_id AND hp.left_at IS NULL
JOIN poliswag.pokemon_name pn ON pn.pokemon_id = h.pokemon_id AND pn.form_id = h.form_id
JOIN trade_player r ON r.discord_id <> h.discord_id AND r.left_at IS NULL AND r.trade_dms = 1
  AND FIND_IN_SET(h.category, r.collecting) AND (pn.is_costume = 0 OR r.show_costumes = 1)
LEFT JOIN collection_entry ce ON ce.discord_id = r.discord_id AND ce.category = h.category
  AND ce.pokemon_id = h.pokemon_id AND ce.form_id = h.form_id
LEFT JOIN trade_dm_notice n ON n.recipient_id = r.discord_id AND n.holder_id = h.discord_id
  AND n.pokemon_id = h.pokemon_id AND n.form_id = h.form_id AND n.category = h.category
WHERE h.list = 'have' AND h.pokemon_id > 0 AND h.trade_for_category IS NULL
  AND h.created_at > %s AND h.created_at <= %s
  AND ce.discord_id IS NULL AND n.recipient_id IS NULL
  AND EXISTS (
    SELECT 1 FROM trade_entry g
    JOIN poliswag.pokemon_name gn ON gn.pokemon_id = g.pokemon_id AND gn.form_id = g.form_id
    LEFT JOIN collection_entry hc ON hc.discord_id = hp.discord_id AND hc.category = g.category
      AND hc.pokemon_id = g.pokemon_id AND hc.form_id = g.form_id
    WHERE g.discord_id = r.discord_id AND g.list = 'have' AND g.pokemon_id > 0
      AND g.trade_for_category IS NULL
      AND FIND_IN_SET(g.category, hp.collecting) AND (gn.is_costume = 0 OR hp.show_costumes = 1)
      AND hc.discord_id IS NULL
  )
ORDER BY r.discord_id, h.discord_id, h.pokemon_id, h.form_id, h.category
"""

# What the recipient could give the holder back: their spares the holder lacks.
_GIVE_BACK_SQL = """
SELECT g.pokemon_id, g.form_id, g.category, gn.name AS pokemon_name, gn.form_name
FROM trade_entry g
JOIN trade_player hp ON hp.discord_id = %s
JOIN poliswag.pokemon_name gn ON gn.pokemon_id = g.pokemon_id AND gn.form_id = g.form_id
LEFT JOIN collection_entry hc ON hc.discord_id = hp.discord_id AND hc.category = g.category
  AND hc.pokemon_id = g.pokemon_id AND hc.form_id = g.form_id
WHERE g.discord_id = %s AND g.list = 'have' AND g.pokemon_id > 0 AND g.trade_for_category IS NULL
  AND FIND_IN_SET(g.category, hp.collecting) AND (gn.is_costume = 0 OR hp.show_costumes = 1)
  AND hc.discord_id IS NULL
ORDER BY g.pokemon_id, g.form_id, g.category
"""


def label(row):
    """ "Pikachu (Fall 2019) · Shiny", as the site and the digest write it."""
    text = row["pokemon_name"] or f"#{row['pokemon_id']}"
    if row.get("form_name"):
        text += f" ({row['form_name']})"
    category = CATEGORY_LABELS.get(row["category"])
    return f"{text} · {category}" if category else text


def items_line(rows):
    names = [label(row) for row in rows[:_ITEMS_PER_LINE]]
    extra = len(rows) - len(names)
    return ", ".join(names) + (f" (+{extra})" if extra > 0 else "")


def message(holders):
    """One DM for one recipient. `holders`: [(holder_id, name, gives, wants)]."""
    shown = holders[:_HOLDERS_PER_DM]
    title = (
        f"🔄 Tens uma troca nova com **{shown[0][1]}**"
        if len(holders) == 1
        else f"🔄 Tens {len(holders)} trocas novas"
    )
    parts = [title]
    base = Config.POKEDEX_URL.rstrip("/")
    for holder_id, name, gives, wants in shown:
        block = [] if len(holders) == 1 else [f"**{name}**"]
        block.append(f"Tem para ti: {items_line(gives)}")
        if wants:
            block.append(f"Quer de ti: {items_line(wants)}")
        block.append(f"<{base}/jogador/{holder_id}?{ORIGIN_TAG}>")
        parts.append("\n".join(block))
    if len(holders) > len(shown):
        parts.append(
            f"E mais {len(holders) - len(shown)}: <{base}/procurar?filtro=mutuais&{ORIGIN_TAG}>"
        )
    parts.append(
        "-# Para não receberes estas mensagens: Pokédex › Perfil e outras opções."
    )
    return "\n\n".join(parts)


class TradeDM:
    def __init__(self, poliswag):
        self.poliswag = poliswag

    async def tick(self):
        batch = await asyncio.to_thread(self._read_batch)
        if batch["initialise"]:
            await asyncio.to_thread(self._advance_watermark, batch["cutoff"])
            return

        by_recipient = defaultdict(list)
        for row in batch["rows"]:
            by_recipient[str(row["recipient_id"])].append(row)
        recipients = list(by_recipient.items())

        for recipient_id, rows in recipients[:_RECIPIENTS_PER_TICK]:
            holders = await asyncio.to_thread(self._holders, recipient_id, rows)
            try:
                user = self.poliswag.get_user(
                    int(recipient_id)
                ) or await self.poliswag.fetch_user(int(recipient_id))
                await user.send(
                    message(holders),
                    allowed_mentions=discord.AllowedMentions.none(),
                    suppress_embeds=True,
                )
            except (discord.Forbidden, discord.NotFound) as e:
                # DMs closed, or the account is gone: recorded anyway, or it
                # would be retried every minute for ever.
                self.poliswag.utility.log_to_file(
                    f"Trade DM to {recipient_id} not delivered: {e}"
                )
            # Recorded the moment it lands, as the announcer does, so one
            # failing recipient can't resend everyone before them.
            await asyncio.to_thread(self._record_notices, rows)

        # Past the cap, the watermark waits: the notices already keep the ones
        # sent from going out twice.
        if len(recipients) <= _RECIPIENTS_PER_TICK:
            await asyncio.to_thread(self._advance_watermark, batch["cutoff"])

    def _connect(self):
        return connect(Config.DB_POGOLEIRIA, dict_rows=True, autocommit=False)

    def _read_batch(self):
        db = self._connect()
        try:
            with db.cursor() as cursor:
                cursor.execute("SELECT " + _CUTOFF_SQL + " AS cutoff")
                cutoff = cursor.fetchone()["cutoff"]
                cursor.execute("SELECT last_trade_dm_at FROM poliswag.poliswag")
                settings = cursor.fetchone()
                watermark = settings["last_trade_dm_at"] if settings else None
                if watermark is None:
                    return {"cutoff": cutoff, "initialise": True, "rows": []}
                cursor.execute(_NEW_MUTUALS_SQL, (watermark, cutoff))
                return {
                    "cutoff": cutoff,
                    "initialise": False,
                    "rows": list(cursor.fetchall()),
                }
        finally:
            db.rollback()
            db.close()

    def _holders(self, recipient_id, rows):
        """Per holder, in order: what they have for the recipient, and what they'd want back."""
        grouped = defaultdict(list)
        for row in rows:
            grouped[str(row["holder_id"])].append(row)
        db = self._connect()
        try:
            out = []
            with db.cursor() as cursor:
                for holder_id, gives in grouped.items():
                    cursor.execute(_GIVE_BACK_SQL, (holder_id, recipient_id))
                    out.append(
                        (
                            holder_id,
                            gives[0]["holder_name"],
                            gives,
                            list(cursor.fetchall()),
                        )
                    )
            return out
        finally:
            db.rollback()
            db.close()

    def _record_notices(self, rows):
        db = self._connect()
        try:
            with db.cursor() as cursor:
                for row in rows:
                    cursor.execute(
                        "INSERT IGNORE INTO trade_dm_notice (recipient_id, holder_id, pokemon_id, form_id, category) VALUES (%s,%s,%s,%s,%s)",
                        (
                            row["recipient_id"],
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
                    "UPDATE poliswag.poliswag SET last_trade_dm_at = %s", (cutoff,)
                )
            db.commit()
        finally:
            db.close()
