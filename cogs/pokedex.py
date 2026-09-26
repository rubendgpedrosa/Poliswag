"""!pokedex — issues a player's login code for the Pokédex (pogoleiria.pt/pokedex).

The code is a password, not a one-time token: it works on as many devices as
the player likes and lasts until they run !pokedex again, which replaces it and
ends every session opened with the old one. It answers to !trades and !trocas
too, the tool's earlier names (Trades, then Trocas, until 2026-09-24).

Identity lives in pogoleiria.trade_player. This cog is also where membership is
tracked — leaving the server hides a player's lists and blocks their login,
rejoining restores them — including a reconcile on startup for whatever changed
while the bot was down.
"""

import asyncio

import discord
from discord.ext import commands

from modules.config import Config
from modules.embeds import build_embed
from modules.trade_digest import build_digest, rows_since
from modules.trade_codes import format_code, generate, hash_code

# How long the in-channel reply lives. Long enough to read on a phone, short
# enough that the channel doesn't fill with them.
CONFIRMATION_SECONDS = 15


class Pokedex(commands.Cog):
    def __init__(self, poliswag):
        self.poliswag = poliswag
        self.store = poliswag.trade_player_store

    async def cog_load(self):
        print(f"{self.__class__.__name__} loaded!")

    async def cog_unload(self):
        print(f"{self.__class__.__name__} unloaded!")

    @commands.command(
        name="pokedex",
        # The tool's earlier names; what the server already learned, and it
        # costs nothing to keep answering to them.
        aliases=["trades", "trocas"],
        brief="Envia-te por DM o código de acesso à Pokédex",
        help="Gera um código novo e envia-o por mensagem privada. Podes "
        "escrevê-lo aqui ou em DM ao bot; num canal, a mensagem é apagada "
        "logo. O código serve como password na Pokédex do site e funciona em "
        "vários dispositivos. Cada !pokedex gera um novo e desliga o antigo.",
    )
    async def pokedex(self, ctx):
        author = ctx.author

        # In a channel, being here is proof of membership. In a DM it is not:
        # the DM channel outlives the membership that created it, so without
        # this someone who left — or was removed — could type !pokedex and have
        # upsert() clear their left_at, undoing the block on their way out.
        if ctx.guild is None and not self.is_member(author.id):
            await ctx.send(
                embed=build_embed(
                    "POKÉDEX",
                    "A Pokédex é para membros do servidor PoGo Leiria. "
                    "Entra no servidor e escreve `!pokedex` outra vez.",
                )
            )
            return

        code = format_code(generate())
        # Tagged for the site's stats (see trade_digest.ORIGIN_TAG).
        link = f"{Config.POKEDEX_URL}/entrar/{code.replace('-', '')}?o=pokedex-login"

        # First, before anything can go wrong: a !pokedex sitting in a channel
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
                    "POKÉDEX — O TEU CÓDIGO",
                    f"🔑 Toca para entrares: {link}\n\n"
                    "Noutro dispositivo, copia o código da mensagem a seguir "
                    "e escreve-o no site.\n\n"
                    "É a tua password: funciona em vários dispositivos ao "
                    "mesmo tempo e dura até pedires outro com `!pokedex`, que "
                    "desliga este.",
                )
            )
            # The code goes in a message of its own, plain and unformatted:
            # on a phone, long-press -> Copy Text copies a whole message, so
            # anything else in here comes along with it. Backticks would too.
            await author.send(code)
        except discord.Forbidden:
            await ctx.send(
                embed=build_embed(
                    "POKÉDEX",
                    f"{author.mention} não consegui enviar-te DM. No servidor "
                    "PoGo Leiria: toca no nome do servidor → **Privacidade** "
                    "→ liga **Mensagens diretas**. Depois escreve `!pokedex` "
                    "outra vez.",
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
                "POKÉDEX", f"{author.mention} enviei-te o código por DM. 📬"
            ),
            delete_after=CONFIRMATION_SECONDS,
        )

    @commands.command(
        name="resumo",
        aliases=["novidades"],
        brief="Envia-te por DM o resumo das novidades nas trocas",
        help="Mostra o mesmo resumo que sai todas as manhãs às 9h, mas para "
        "os últimos dias e só para ti. `!resumo 7` cobre uma semana. O post "
        "das 9h cala-se quando não há nada novo, e isto é como confirmas que "
        "está vivo sem esperar pela manhã seguinte.",
    )
    async def resumo(self, ctx, dias: int = 1):
        author = ctx.author
        if ctx.guild is None and not self.is_member(author.id):
            await ctx.send(
                embed=build_embed(
                    "POKÉDEX",
                    "A Pokédex é para membros do servidor PoGo Leiria.",
                )
            )
            return

        # As !pokedex: the answer goes by DM, so the command has nothing
        # left to say in the channel.
        if ctx.guild is not None:
            try:
                await ctx.message.delete()
            except (discord.Forbidden, discord.NotFound):
                pass

        # A window, not a watermark: a preview must never eat a morning's news.
        dias = max(1, min(30, dias))
        rows = await asyncio.to_thread(rows_since, dias)
        janela = "no último dia" if dias == 1 else f"nos últimos {dias} dias"
        embed = (
            build_digest(rows)
            if rows
            else build_embed("Novidades nas trocas", f"Sem novidades {janela}.")
        )

        try:
            await author.send(embed=embed)
        except discord.Forbidden:
            await ctx.send(
                embed=build_embed(
                    "POKÉDEX",
                    f"{author.mention} não consegui enviar-te DM. No servidor "
                    "PoGo Leiria: toca no nome do servidor → **Privacidade** "
                    "→ liga **Mensagens diretas**.",
                ),
                delete_after=CONFIRMATION_SECONDS,
            )
            return

        if ctx.guild is not None:
            await ctx.send(
                embed=build_embed(
                    "POKÉDEX", f"{author.mention} enviei-te o resumo por DM. 📬"
                ),
                delete_after=CONFIRMATION_SECONDS,
            )

    def is_member(self, user_id):
        """Is this user in a guild the bot serves?

        Reads the member cache rather than fetching: `Intents.all()` keeps it
        populated, and a login command shouldn't wait on an API call.

        Fails open when no guild has any member cached at all — the same call
        reconcile() makes, for the same reason. An empty cache is the bot's
        problem, and locking every player out of their lists over it is worse
        than the hole it would close.
        """
        guilds = list(getattr(self.poliswag, "guilds", None) or [])
        cached = False
        for guild in guilds:
            if guild.get_member(user_id) is not None:
                return True
            cached = cached or bool(getattr(guild, "members", None))
        return not cached

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
            print(f"Pokédex reconcile: {len(left)} left, {len(returned)} returned")

    @commands.Cog.listener()
    async def on_ready(self):
        await self.reconcile()


async def setup(poliswag):
    await poliswag.add_cog(Pokedex(poliswag))
