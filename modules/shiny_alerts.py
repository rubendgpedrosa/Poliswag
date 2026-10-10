"""Shiny predictions by direct message.

Two jobs each scheduler tick:

1. Tell a member their friend code resolved (or failed). The host script
   scripts/shiny_linker writes the result; this sends one DM and stamps
   announced_at.
2. For linked players in Config.SHINY_ALERT_USER_IDS, roll every new spawn
   (modules/shiny_roll.py) and DM the ones that come up 0.

Owns shiny_alert and poliswag.shiny_alert_watermark; reads shiny_link,
golbat's pokemon and pokemon_shiny_stats, and pokemon_name.
"""

import time
from datetime import datetime
from zoneinfo import ZoneInfo

import discord

from modules.config import Config
from modules.embeds import build_embed
from modules.shiny_roll import shiny_die
from modules.shiny_store import ShinyStore

_LISBON = ZoneInfo("Europe/Lisbon")
# A spawn with less than this left is not worth a DM.
MIN_SECONDS_LEFT = 120
# Lure and tappable spawns are left out (the roll is untested on them), and
# cell-only sightings have no location worth sending.
_SPAWNS_SQL = f"""
SELECT id, pokemon_id, COALESCE(form, 0) AS form_id, lat, lon, expire_timestamp,
       expire_timestamp_verified, seen_type, first_seen_timestamp
FROM {Config.DB_SCANNER_NAME}.pokemon
WHERE first_seen_timestamp >= %s
  AND (expire_timestamp IS NULL OR expire_timestamp >= %s)
  AND seen_type IN ('wild', 'encounter', 'nearby_stop')
ORDER BY first_seen_timestamp
"""
_SEEN_SHINY_SQL = f"""
SELECT 1 AS seen FROM {Config.DB_SCANNER_NAME}.pokemon_shiny_stats
WHERE pokemon_id = %s AND form_id = %s AND `count` > 0 LIMIT 1
"""


def link_message(row, alerts_on):
    """The one DM a member gets when the linker is done with their code."""
    if row["status"] == "linked":
        text = (
            f"✨ O teu código ficou associado ao treinador **{row['trainer_name']}**."
        )
        if not alerts_on:
            text += (
                "\nOs alertas de shiny ainda estão em teste; aviso-te quando abrirem."
            )
        return text + "\n-# Não és tu? Escreve `!shiny remover` e envia o código certo."
    return (
        f"Não consegui associar o teu código: {row['fail_reason']}. "
        "Confirma-o no jogo e envia `!shiny <código>` outra vez."
    )


def _despawn_line(spawn):
    expires = spawn["expire_timestamp"]
    if not expires:
        return "⏳ Hora de desaparecer desconhecida"
    hour = datetime.fromtimestamp(expires, _LISBON).strftime("%H:%M")
    if spawn["expire_timestamp_verified"]:
        return f"⏳ Desaparece às {hour}"
    return f"⏳ Desaparece por volta das {hour} (estimado)"


def alert_embed(spawn, name, seen_shiny):
    lines = [
        f"📍 [Abrir no mapa](https://www.google.com/maps?q={spawn['lat']},{spawn['lon']})",
        _despawn_line(spawn),
        (
            "✅ O scanner já viu este Pokémon shiny."
            if seen_shiny
            else "⚠️ O scanner nunca viu este Pokémon shiny: pode não existir em shiny."
        ),
    ]
    if spawn["seen_type"] == "nearby_stop":
        lines.append("📡 Localização aproximada (a pokéstop mais próxima).")
    return build_embed(
        f"✨ {name} deve ser shiny para ti",
        "\n".join(lines),
        footer="Previsão em teste. Diz-me se acertei.",
    )


def verdict_view(encounter_id):
    """Era shiny / Não era. No callbacks: cogs/shiny.py answers the custom
    IDs in on_interaction, so the buttons outlive a bot restart."""
    view = discord.ui.View(timeout=None)
    view.add_item(
        discord.ui.Button(
            label="Era shiny",
            style=discord.ButtonStyle.success,
            custom_id=f"shiny:s:{encounter_id}",
        )
    )
    view.add_item(
        discord.ui.Button(
            label="Não era",
            style=discord.ButtonStyle.secondary,
            custom_id=f"shiny:n:{encounter_id}",
        )
    )
    return view


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
            _SPAWNS_SQL, params=(watermark, now + MIN_SECONDS_LEFT)
        )
        for spawn in spawns:
            for recipient in recipients:
                try:
                    roll = shiny_die(spawn["id"], recipient["player_id"])
                except ValueError:
                    break  # not an encounter ID; no recipient can roll it
                if roll == 0:
                    await self._alert(recipient["discord_id"], spawn)
        if spawns:
            # `>=` above re-reads the newest second next tick, in case more
            # rows land in it; shiny_alert keeps those from going out twice.
            await self.store.set_watermark(
                max(s["first_seen_timestamp"] for s in spawns)
            )

    async def _alert(self, discord_id, spawn):
        # Recorded before sending: a spawn told twice is worse than one lost
        # to a failed send.
        if not await self.store.record_alert(
            discord_id, spawn["id"], spawn["pokemon_id"], spawn["form_id"]
        ):
            return
        key = (spawn["pokemon_id"], spawn["form_id"])
        names = await self.poliswag.db.get_data_from_database(
            "SELECT name, form_name FROM pokemon_name WHERE pokemon_id = %s AND form_id = %s",
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
        await self._dm(
            discord_id,
            embed=alert_embed(spawn, name, bool(seen)),
            view=verdict_view(spawn["id"]),
        )

    async def _announce_links(self):
        for row in await self.store.unannounced():
            alerts_on = row["discord_id"] in Config.SHINY_ALERT_USER_IDS
            await self._dm(row["discord_id"], content=link_message(row, alerts_on))
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
