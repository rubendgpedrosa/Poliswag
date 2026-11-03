"""Apoio à comunidade: `!mbway`.

The site's support card points at Revolut; this is the other route, for the
people who would rather send an MB Way transfer. The number is the owner's, so
it lives in MBWAY_DETAILS in the protected .env and never in the repository —
changing it means editing an env var and restarting, not a code change.

It answers by DM in every case. In a channel the command message goes first
(the same reason as `!pokedex`: an `!mbway` left sitting in a channel tells
every member, and every bot with message access, that a payment route was
asked for), then the details land in the DM. With DMs closed the command says
so without naming anything.
"""

import discord
from discord.ext import commands

from modules.config import Config
from modules.embeds import build_embed
from modules.mbway_requests import owner_text, record, where_text

UNSET = (
    "O MB Way não está disponível de momento. "
    "Podes apoiar via Revolut: https://revolut.me/rubendgpedrosa"
)

_DM_CLOSED = (
    "{author} não consegui enviar-te DM. No servidor PoGo Leiria: toca no nome "
    "do servidor → **Privacidade** → liga **Mensagens diretas**. Depois escreve "
    "`!mbway` outra vez."
)


class Support(commands.Cog):
    def __init__(self, poliswag):
        self.poliswag = poliswag

    async def cog_load(self):
        print(f"{self.__class__.__name__} loaded!")

    async def cog_unload(self):
        print(f"{self.__class__.__name__} unloaded!")

    @commands.command(
        name="mbway",
        brief="Envia os dados de apoio por MB Way",
        help="Utilização: `!mbway`. Os dados de apoio vão por mensagem "
        "direta; em canal, a mensagem do comando é apagada.",
    )
    async def mbway(self, ctx):
        # First, before anything can go wrong: the command message in a
        # channel names the payment route to everyone reading it. A DM has
        # nothing to hide, and the bot can't delete someone else's message
        # there anyway.
        if ctx.guild is not None:
            try:
                await ctx.message.delete()
            except (discord.Forbidden, discord.NotFound):
                pass

        details = (Config.MBWAY_DETAILS or "").strip()
        await self._send(ctx, details or UNSET)
        await self._note_owner(ctx, unset=not details)

    async def _note_owner(self, ctx, unset):
        """Tells the owner who asked and how many times before.

        Runs after the member already has the answer, so a database or DM
        failure here costs the owner a notification and nothing else.
        """
        owner_id = getattr(Config, "MY_ID", 0) or 0
        if not owner_id:
            return
        try:
            tally = await record(self.poliswag.db, ctx.author)
            user = self.poliswag.get_user(owner_id)
            if user is None:
                user = await self.poliswag.fetch_user(owner_id)
            if user is None:
                return
            await user.send(
                embed=build_embed(
                    "PEDIDO DE MB WAY",
                    owner_text(ctx.author, tally, where_text(ctx), unset=unset),
                )
            )
        except Exception as exc:  # noqa: BLE001 - never break the member's flow
            print(f"MbWay: não avisei o dono ({exc})")

    async def _send(self, ctx, text):
        embed = build_embed("APOIO POR MB WAY", text)
        try:
            await ctx.author.send(embed=embed)
        except discord.Forbidden:
            await ctx.send(
                embed=build_embed(
                    "APOIO POR MB WAY",
                    _DM_CLOSED.format(author=ctx.author.mention),
                )
            )


async def setup(poliswag):
    await poliswag.add_cog(Support(poliswag))
