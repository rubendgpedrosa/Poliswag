"""Restart-safe moderation for the website's shiny-map access requests."""

import asyncio

import discord
from discord.ext import commands

from modules.config import Config
from modules.shiny_access_requests import (
    PREFIX,
    decide_request,
    decision_embed,
    parse_button,
)


class ShinyAccessRequests(commands.Cog):
    def __init__(self, poliswag):
        self.poliswag = poliswag

    @commands.Cog.listener()
    async def on_interaction(self, interaction):
        custom_id = (interaction.data or {}).get("custom_id", "")
        if not isinstance(custom_id, str) or not custom_id.startswith(PREFIX):
            return
        parsed = parse_button(custom_id)
        if not parsed:
            await interaction.response.send_message("Pedido inválido.", ephemeral=True)
            return
        authorized = str(interaction.user.id) == str(Config.MY_ID) or str(
            interaction.user.id
        ) in {str(value).strip() for value in self.poliswag.ADMIN_USERS_IDS}
        if (
            not authorized
            or interaction.guild is None
            or interaction.channel_id != Config.MOD_CHANNEL_ID
            or interaction.message is None
            or interaction.message.author.id != self.poliswag.user.id
        ):
            await interaction.response.send_message(
                "Só os moderadores podem decidir pedidos no canal de moderação.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        action, discord_id, stamp = parsed
        try:
            changed = await asyncio.to_thread(
                decide_request, discord_id, stamp, action == "accept"
            )
        except Exception as error:
            self.poliswag.utility.log_to_file(
                f"[SHINY ACCESS] Decision failed for {discord_id}: {error}", "ERROR"
            )
            await interaction.followup.send(
                "Não foi possível guardar a decisão. Tenta novamente.", ephemeral=True
            )
            return
        if not changed:
            await interaction.followup.send(
                "Este pedido já foi resolvido ou deixou de estar pendente.",
                ephemeral=True,
            )
            return
        decided_at = discord.utils.utcnow()
        verdict = "Aceite" if action == "accept" else "Rejeitado"
        try:
            embed = (
                interaction.message.embeds[0]
                if interaction.message.embeds
                else discord.Embed(title="Pokédex: pedido de acesso ao mapa de shinies")
            )
            embed = decision_embed(
                embed, interaction.user, action == "accept", decided_at
            )
            await interaction.message.edit(
                embed=embed, view=None, allowed_mentions=discord.AllowedMentions.none()
            )
        except discord.HTTPException as error:
            self.poliswag.utility.log_to_file(
                f"[SHINY ACCESS] Decision saved; message edit failed: {error}", "ERROR"
            )
            await interaction.followup.send(
                f"Pedido {verdict.lower()}. A decisão foi guardada, mas não foi possível atualizar a mensagem.",
                ephemeral=True,
            )
            return
        await interaction.followup.send(f"Pedido {verdict.lower()}.", ephemeral=True)


async def setup(poliswag):
    await poliswag.add_cog(ShinyAccessRequests(poliswag))
