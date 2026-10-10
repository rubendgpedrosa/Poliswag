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

import discord

from modules.config import Config
from modules.shiny_store import ShinyStore


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


class ShinyAlerts:
    def __init__(self, poliswag):
        self.poliswag = poliswag
        self.store = ShinyStore(poliswag.db)

    async def tick(self):
        await self._announce_links()

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
