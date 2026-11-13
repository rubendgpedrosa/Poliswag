"""Aceitar / Cancelar on the nightly update plan (modules.update_plan)."""

import discord
from discord.ext import commands

from modules import update_plan
from modules.config import Config


class UpdatePlan(commands.Cog):
    def __init__(self, poliswag):
        self.poliswag = poliswag

    @commands.Cog.listener()
    async def on_interaction(self, interaction):
        custom_id = (interaction.data or {}).get("custom_id", "")
        if not isinstance(custom_id, str) or not custom_id.startswith(
            update_plan.PREFIX
        ):
            return
        if str(interaction.user.id) != str(Config.MY_ID):
            await interaction.response.send_message(
                "Só o dono pode decidir as atualizações.", ephemeral=True
            )
            return
        _, action, plan_id = custom_id.split(":", 2)
        state = update_plan.decide(update_plan.load(), action, plan_id)
        if state is None:
            await interaction.response.send_message(
                "Este plano já não está pendente.", ephemeral=True
            )
            return
        update_plan.save(state)
        self.poliswag.utility.log_to_file(f"[UPDATE PLAN] {plan_id}: {state['status']}")
        await interaction.response.edit_message(
            embed=discord.Embed.from_dict(update_plan.embed(state)),
            view=_view(state),
        )


def _view(state):
    """The same buttons as the REST message, as a discord.py view."""
    view = discord.ui.View(timeout=None)
    for row in update_plan.components(state):
        for button in row["components"]:
            view.add_item(
                discord.ui.Button(
                    label=button["label"],
                    custom_id=button["custom_id"],
                    style=discord.ButtonStyle(button["style"]),
                )
            )
    return view


async def setup(poliswag):
    await poliswag.add_cog(UpdatePlan(poliswag))
