"""!trocas — issues a player's login code for the trades tool.

The code is a password, not a one-time token: it works on as many devices as
the player likes and lasts until they run !trocas again, which replaces it and
ends every session opened with the old one.

Identity lives in pogoleiria.trade_player. This cog is also where membership is
tracked — leaving the server hides a player's lists and blocks their login,
rejoining restores them — including a reconcile on startup for whatever changed
while the bot was down.
"""

import discord
from discord.ext import commands

from modules.config import Config
from modules.embeds import build_embed
from modules.trade_codes import format_code, generate, hash_code

# How long the in-channel reply lives. Long enough to read on a phone, short
# enough that the channel doesn't fill with them.
CONFIRMATION_SECONDS = 15


class Trades(commands.Cog):
    def __init__(self, poliswag):
        self.poliswag = poliswag
        self.store = poliswag.trade_player_store

    async def cog_load(self):
        print(f"{self.__class__.__name__} loaded!")

    async def cog_unload(self):
        print(f"{self.__class__.__name__} unloaded!")

    @commands.command(
        name="trocas",
        brief="Envia-te por DM o código de acesso às trocas",
        help="Gera um código novo e envia-o por mensagem privada. Podes "
        "escrevê-lo aqui ou em DM ao bot; num canal, a mensagem é apagada "
        "logo. O código serve como password no site das trocas e funciona em "
        "vários dispositivos. Cada !trocas gera um novo e desliga o antigo.",
    )
    async def trocas(self, ctx):
        author = ctx.author
        code = format_code(generate())
        link = f"{Config.TRADES_URL}/entrar/{code.replace('-', '')}"

        # First, before anything can go wrong: a !trocas sitting in a channel
        # tells everyone this player just took a fresh code, and the reply
        # below points at it. In a DM there is nothing to hide and the bot
        # can't delete someone else's message anyway.
        if ctx.guild is not None:
            try:
                await ctx.message.delete()
            except (discord.Forbidden, discord.NotFound):
                # Missing Manage Messages, or someone deleted it first.
                pass

        try:
            await author.send(
                embed=build_embed(
                    "TROCAS — O TEU CÓDIGO",
                    f"🔑 `{code}`\n\n"
                    f"Entrar: {link}\n\n"
                    "Guarda-o: serve como password, e podes usá-lo em vários "
                    "dispositivos. `!trocas` gera um novo, desativa este e "
                    "termina a sessão em todos.",
                )
            )
        except discord.Forbidden:
            await ctx.send(
                embed=build_embed(
                    "TROCAS",
                    f"{author.mention} não consegui enviar-te DM. Ativa "
                    "*Mensagens privadas de membros do servidor* nas "
                    "definições de privacidade e tenta outra vez.",
                ),
                delete_after=CONFIRMATION_SECONDS,
            )
            return

        await self.store.upsert(
            discord_id=author.id,
            username=author.name,
            display_name=author.display_name,
            avatar_url=str(author.display_avatar.url),
            code_hash=hash_code(code),
        )
        # In a DM the code has already arrived in this same conversation, so a
        # second "check your DMs" message would just be noise.
        if ctx.guild is None:
            return

        await ctx.send(
            embed=build_embed(
                "TROCAS", f"{author.mention} enviei-te o código por DM. 📬"
            ),
            delete_after=CONFIRMATION_SECONDS,
        )

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        await self.store.set_left(member.id)

    @commands.Cog.listener()
    async def on_member_join(self, member):
        await self.store.clear_left(member.id)

    @commands.Cog.listener()
    async def on_member_update(self, _before, after):
        await self.store.refresh_identity(
            after.id, after.name, after.display_name, str(after.display_avatar.url)
        )

    async def reconcile(self):
        """Align the table with who is in the guild. Called from on_ready."""
        member_ids = {m.id for guild in self.poliswag.guilds for m in guild.members}
        if not member_ids:
            return
        left, returned = await self.store.reconcile(member_ids)
        if left or returned:
            print(f"Trades reconcile: {len(left)} left, {len(returned)} returned")

    @commands.Cog.listener()
    async def on_ready(self):
        await self.reconcile()


async def setup(poliswag):
    await poliswag.add_cog(Trades(poliswag))
