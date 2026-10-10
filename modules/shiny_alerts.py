"""Shiny predictions by direct message.

Two jobs each scheduler tick:

1. Tell a pilot user (Config.SHINY_ALERT_USER_IDS) their friend code resolved
   or failed. The host script scripts/shiny_linker writes the result; this
   sends one DM and stamps announced_at. Anyone else's link is stamped
   without a DM: during the pilot the bot messages nobody but its users.
2. For linked players in Config.SHINY_ALERT_USER_IDS, roll every new spawn
   (modules/shiny_roll.py) and DM the ones that come up 0.

Owns shiny_alert and poliswag.shiny_alert_watermark; reads shiny_link,
golbat's pokemon and pokemon_shiny_stats, and pokemon_name.
"""

import json
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import discord

from modules.config import Config
from modules.embeds import build_embed
from modules.poracle_client import PoracleError
from modules.shiny_roll import shiny_die
from modules.shiny_store import ShinyStore

_LISBON = ZoneInfo("Europe/Lisbon")
# A spawn with less than this left is not worth a DM.
MIN_SECONDS_LEFT = 120
# A sighting has no IV until a scanner account encounters it, usually within
# a minute or two. An alert waits this long for it, then goes out without.
WAIT_FOR_IV = 180
# How far behind the watermark each tick reads, so a waiting spawn is seen
# again; shiny_alert keeps the ones already sent from going out twice.
LOOK_BACK = 300
# Which spawns are rolled follows Golbat PR #423: only ones the scanner has
# seen itself (wild or encountered), never Ditto or anything shown as another
# species, and not one whose costume is unknown.
_SPAWNS_SQL = f"""
SELECT id, pokemon_id, COALESCE(form, 0) AS form_id, lat, lon, expire_timestamp,
       expire_timestamp_verified, seen_type, first_seen_timestamp,
       iv, atk_iv, def_iv, sta_iv, cp, level, costume, gender, move_1, move_2,
       weight, height, size, weather, spawn_id, pokestop_id, is_event, updated, pvp
FROM {Config.DB_SCANNER_NAME}.pokemon
WHERE first_seen_timestamp >= %s
  AND (expire_timestamp IS NULL OR expire_timestamp >= %s)
  AND seen_type IN ('wild', 'encounter')
  AND pokemon_id <> 132 AND is_ditto = 0
  AND (display_pokemon_id IS NULL OR display_pokemon_id IN (0, pokemon_id))
  AND COALESCE(costume, 0) <> 255
ORDER BY first_seen_timestamp
"""
_SEEN_SHINY_SQL = f"""
SELECT 1 AS seen FROM {Config.DB_SCANNER_NAME}.pokemon_shiny_stats
WHERE pokemon_id = %s AND form_id = %s AND `count` > 0 LIMIT 1
"""


def link_message(row):
    """The one DM a pilot user gets when the linker is done with their code."""
    if row["status"] == "linked":
        return (
            f"✨ O teu código ficou associado ao treinador **{row['trainer_name']}**."
            "\n-# Não és tu? Escreve `!shiny remover` e envia o código certo."
        )
    return (
        f"Não consegui associar o teu código: {row['fail_reason']}. "
        "Confirma-o no jogo e envia `!shiny <código>` outra vez."
    )


def plays_there(recipient, spawn):
    """Does this player want spawns where this one is? Marinha Grande is west
    of Config.MARINHA_LON_MAX, Leiria east, the same line the quests use."""
    in_marinha = float(spawn["lon"]) <= Config.MARINHA_LON_MAX
    return bool(recipient["alert_marinha"] if in_marinha else recipient["alert_leiria"])


# Poracle's DTS entry for these alerts (monster / "shiny" in its dts.json; a
# copy is kept in templates/poracle/shiny-monster.json). It is Poracle's
# standard Pokémon alert with a "Potencialmente shiny para ti" line on top.
PORACLE_TEMPLATE = "shiny"


def poracle_webhook(spawn):
    """The spawn as Golbat would have sent it to Poracle, so Poracle renders
    it exactly like its own alerts: map tile, IV, moves, address."""
    try:
        pvp = json.loads(spawn["pvp"]) if spawn.get("pvp") else None
    except (TypeError, ValueError):
        pvp = None

    def number(value):
        return float(value) if value is not None else None

    return {
        "encounter_id": str(spawn["id"]),
        "pokemon_id": spawn["pokemon_id"],
        "form": spawn["form_id"],
        "costume": spawn.get("costume") or 0,
        "gender": spawn.get("gender"),
        "latitude": float(spawn["lat"]),
        "longitude": float(spawn["lon"]),
        "disappear_time": spawn["expire_timestamp"],
        "disappear_time_verified": bool(spawn["expire_timestamp_verified"]),
        "first_seen": spawn["first_seen_timestamp"],
        "last_modified_time": spawn.get("updated"),
        "individual_attack": spawn["atk_iv"],
        "individual_defense": spawn["def_iv"],
        "individual_stamina": spawn["sta_iv"],
        "cp": spawn["cp"],
        "pokemon_level": spawn["level"],
        "move_1": spawn.get("move_1"),
        "move_2": spawn.get("move_2"),
        "weight": number(spawn.get("weight")),
        "height": number(spawn.get("height")),
        "size": spawn.get("size"),
        "weather": spawn.get("weather") or 0,
        "spawnpoint_id": (
            format(spawn["spawn_id"], "x") if spawn.get("spawn_id") else "None"
        ),
        "pokestop_id": spawn.get("pokestop_id") or "None",
        "is_event": spawn.get("is_event") or 0,
        "seen_type": spawn["seen_type"],
        "pvp": pvp,
    }


def _despawn_line(spawn):
    expires = spawn["expire_timestamp"]
    if not expires:
        return "⏳ Hora de desaparecer desconhecida"
    hour = datetime.fromtimestamp(expires, _LISBON).strftime("%H:%M")
    if spawn["expire_timestamp_verified"]:
        return f"⏳ Desaparece às {hour}"
    return f"⏳ Desaparece por volta das {hour} (estimado)"


def _iv_line(spawn):
    if spawn["iv"] is None:
        return "💪 IV ainda desconhecido"
    line = (
        f"💪 IV **{round(spawn['iv'])}%** "
        f"({spawn['atk_iv']}/{spawn['def_iv']}/{spawn['sta_iv']})"
    )
    if spawn["cp"]:
        line += f" · CP {spawn['cp']}"
    if spawn["level"]:
        line += f" · nível {spawn['level']}"
    return line


def alert_embed(spawn, name, seen_shiny):
    lines = [
        "✨ **Potencialmente shiny para ti**",
        _iv_line(spawn),
        f"📍 [Abrir no mapa](https://www.google.com/maps?q={spawn['lat']},{spawn['lon']})",
        _despawn_line(spawn),
        (
            "✅ O scanner já viu este Pokémon shiny."
            if seen_shiny
            else "⚠️ O scanner nunca viu este Pokémon shiny: pode não existir em shiny."
        ),
    ]
    headline = name if spawn["iv"] is None else f"{name} {round(spawn['iv'])}%"
    return build_embed(f"✨ {headline}", "\n".join(lines))


class ShinyAlerts:
    def __init__(self, poliswag):
        self.poliswag = poliswag
        self.store = ShinyStore(poliswag.db)

    async def tick(self):
        await self._announce_links()
        await self._alert_spawns()

    async def _alert_spawns(self):
        now = int(time.time())
        watermark = await self.store.watermark()
        recipients = await self.store.recipients(Config.SHINY_ALERT_USER_IDS)
        if watermark is None or not recipients:
            # Never run, or nobody to tell: start from now, so the first
            # linked player isn't sent everything already on the map.
            await self.store.set_watermark(now)
            return

        spawns = await self.poliswag.db.get_data_from_database(
            _SPAWNS_SQL, params=(watermark - LOOK_BACK, now + MIN_SECONDS_LEFT)
        )
        for spawn in spawns:
            for recipient in recipients:
                if not plays_there(recipient, spawn):
                    continue
                try:
                    roll = shiny_die(spawn["id"], recipient["player_id"])
                except ValueError:
                    break  # not an encounter ID; no recipient can roll it
                if roll != 0:
                    continue
                waited = now - spawn["first_seen_timestamp"]
                if spawn["iv"] is None and waited < WAIT_FOR_IV:
                    continue  # read again next tick, IV in hand or time up
                await self._alert(recipient, spawn)
        if spawns:
            # `>=` above re-reads the newest second next tick, in case more
            # rows land in it; shiny_alert keeps those from going out twice.
            await self.store.set_watermark(
                max(s["first_seen_timestamp"] for s in spawns)
            )

    async def _alert(self, recipient, spawn):
        discord_id = recipient["discord_id"]
        # Recorded before sending: a spawn told twice is worse than one lost
        # to a failed send.
        if not await self.store.record_alert(
            discord_id, spawn["id"], spawn["pokemon_id"], spawn["form_id"]
        ):
            return
        if await self._through_poracle(recipient, spawn):
            return
        # Poracle could not take it: the bot's own, plainer embed instead.
        key = (spawn["pokemon_id"], spawn["form_id"])
        # Golbat names a species' ordinary form by its own id (Zorua is 2108);
        # pokemon_name files that form under 0, so 0 is the fallback.
        names = await self.poliswag.db.get_data_from_database(
            "SELECT name, form_name FROM pokemon_name WHERE pokemon_id = %s "
            "AND form_id IN (%s, 0) ORDER BY form_id DESC LIMIT 1",
            params=key,
        )
        name = f"#{spawn['pokemon_id']}"
        if names:
            name = names[0]["name"]
            if names[0]["form_name"]:
                name += f" ({names[0]['form_name']})"
        seen = await self.poliswag.db.get_data_from_database(
            _SEEN_SHINY_SQL, params=key
        )
        embed = alert_embed(spawn, name, bool(seen))
        channel = (
            self.poliswag.get_channel(int(recipient["channel_id"]))
            if recipient.get("channel_id")
            else None
        )
        if channel is not None:
            await channel.send(embed=embed)
        else:
            await self._dm(discord_id, embed=embed)

    async def _through_poracle(self, recipient, spawn):
        """Have Poracle render and deliver the alert, as it does its own.
        Its test endpoint is the one that takes a spawn and a destination
        and skips matching; nothing about the result says "test"."""
        name = recipient.get("discord_name") or str(recipient["discord_id"])
        # Their own channel if they have one (`!shiny canal`), else a DM.
        if recipient.get("channel_id"):
            destination = {
                "id": str(recipient["channel_id"]),
                "name": f"shiny-{name}",
                "type": "discord:channel",
            }
        else:
            destination = {
                "id": str(recipient["discord_id"]),
                "name": name,
                "type": "discord:user",
            }
        target = {**destination, "language": "en", "template": PORACLE_TEMPLATE}
        try:
            await self.poliswag.poracle.test_pokemon(poracle_webhook(spawn), target)
            return True
        except PoracleError as e:
            self.poliswag.utility.log_to_file(
                f"Shiny alert for {recipient['discord_id']} not sent through Poracle: {e}",
                "ERROR",
            )
            return False

    async def _announce_links(self):
        for row in await self.store.unannounced():
            # Only the pilot's users hear from the bot about shinies. A link
            # the owner made for someone else is closed without a word.
            if row["discord_id"] in Config.SHINY_ALERT_USER_IDS:
                await self._dm(row["discord_id"], content=link_message(row))
            # Stamped even when the DM was refused, or it would be retried
            # every minute for ever; `!shiny` still shows the status.
            await self.store.mark_announced(row["discord_id"])

    async def _dm(self, discord_id, **message):
        try:
            user = self.poliswag.get_user(
                int(discord_id)
            ) or await self.poliswag.fetch_user(int(discord_id))
            await user.send(**message, allowed_mentions=discord.AllowedMentions.none())
        except (discord.Forbidden, discord.NotFound) as e:
            self.poliswag.utility.log_to_file(
                f"Shiny DM to {discord_id} not delivered: {e}"
            )
