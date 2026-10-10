"""!shiny — a member hands over a friend code so shinies can be predicted.

The code is only the way in: the host script scripts/shiny_linker has a
scanner account look the trainer up by that code, invite them to a private
raid lobby and read the internal player ID the game's shiny roll uses
(modules/shiny_roll.py). This cog queues the code and reports status;
modules/shiny_alerts.py tells the member when it resolves.

Nothing proves a code belongs to whoever sent it. The resolution DM names
the trainer it matched, which is what makes a wrong code visible.
"""

import hashlib
import secrets

import discord
from discord.ext import commands

from modules.config import Config
from modules.embeds import build_embed
from modules.membership import is_member
from modules.permissions import owner_only
from modules.shiny_roll import normalise_friend_code
from modules.shiny_store import ShinyStore

TITLE = "SHINY"
# The category the players' private channels are created under.
CHANNEL_CATEGORY = "Shinies"
_WHEN = (
    "quando houver uma raid ativa na zona (durante o dia, normalmente em "
    "menos de uma hora)"
)
# custom_id "shiny:<key>:<encounter id>": the Era shiny / Não era buttons that
# alerts carried on 2026-10-10. New alerts have none; these still answer the
# old ones.
_VERDICTS = {"s": ("shiny", "era shiny ✅"), "n": ("not_shiny", "não era ❌")}


class Shiny(commands.Cog):
    def __init__(self, poliswag):
        self.poliswag = poliswag
        self.store = ShinyStore(poliswag.db)

    async def cog_load(self):
        print(f"{self.__class__.__name__} loaded!")

    async def cog_unload(self):
        print(f"{self.__class__.__name__} unloaded!")

    @commands.command(
        name="shiny",
        brief="Associa o teu código de amigo às previsões de shiny",
        help="`!shiny 1234 5678 9012` envia o teu código de amigo; uma conta "
        "do scanner procura o teu treinador e eu aviso-te por DM quando "
        "estiver associado. `!shiny` mostra o estado e `!shiny remover` "
        "apaga tudo. `!shiny mapa [nome]` envia por DM o link do mapa de um "
        "jogador associado e `!shiny canal [nome]` cria-lhe um canal privado "
        "com o mapa e os alertas. Funciona aqui ou em DM ao bot.",
    )
    # Pilot: the owner alone, until the predictions have been checked in the
    # field. Removing this line opens the command to every member.
    @owner_only()
    async def shiny(self, ctx, *, texto: str = ""):
        author = ctx.author
        if ctx.guild is None and not is_member(self.poliswag, author.id):
            await self._say(
                ctx, "As previsões de shiny são para membros do servidor PoGo Leiria."
            )
            return

        texto = texto.strip()
        if not texto:
            await self._say(ctx, self._status_text(await self.store.get(author.id)))
            return
        if texto.lower().split()[0] == "canal":
            await self._open_channel(ctx, texto[5:].strip() or author.name)
            return
        if texto.lower().split()[0] == "mapa":
            await self._send_map_link(ctx, texto[4:].strip() or author.name)
            return
        if texto.lower() == "remover":
            await self.store.remove(author.id)
            await self._say(ctx, "Apaguei o teu código e o histórico de alertas.")
            return

        code = normalise_friend_code(texto)
        if code is None:
            await self._say(
                ctx,
                "Isso não parece um código de amigo. São 12 dígitos, por "
                "exemplo `!shiny 1234 5678 9012`.",
            )
            return
        await self.store.request(author.id, author.name, code)
        await self._say(
            ctx,
            f"Código recebido. Uma conta do scanner vai procurar o teu treinador {_WHEN} "
            "e eu aviso-te por DM. Podes receber um convite de raid no jogo: ignora-o.",
        )

    @commands.Cog.listener()
    async def on_interaction(self, interaction):
        """The Era shiny / Não era buttons under an alert DM.

        The verdict is stored against whoever pressed, so it can only ever
        answer that person's own alert row.
        """
        parts = ((interaction.data or {}).get("custom_id") or "").split(":")
        if len(parts) != 3 or parts[0] != "shiny" or parts[1] not in _VERDICTS:
            return
        verdict, label = _VERDICTS[parts[1]]
        stored = await self.store.set_verdict(interaction.user.id, parts[2], verdict)
        embeds = interaction.message.embeds
        embed = embeds[0] if embeds else None
        if embed is not None:
            embed.set_footer(
                text=(
                    f"Resposta registada: {label}"
                    if stored
                    else "Já tinhas respondido a este alerta."
                )
            )
        await interaction.response.edit_message(
            embed=embed, view=self._without_verdict_buttons(interaction.message)
        )

    @staticmethod
    def _without_verdict_buttons(message):
        """The message's link buttons (Poracle's Google / Apple row), without
        the two that were just answered. None when nothing is left."""
        view = discord.ui.View(timeout=None)
        for row in message.components:
            for button in getattr(row, "children", []):
                if getattr(button, "url", None):
                    view.add_item(
                        discord.ui.Button(
                            label=button.label, url=button.url, emoji=button.emoji
                        )
                    )
        return view if view.children else None

    async def _map_link_embed(self, player):
        """A fresh link to a linked player's private map, as an embed.

        The URL is the credential (pogoleiria.pt/shiny/<token>); only its
        SHA-256 is stored, and each new link ends the one before it.
        """
        token = secrets.token_hex(32)
        await self.store.set_map_token(
            player["discord_id"], hashlib.sha256(token.encode()).hexdigest()
        )
        return build_embed(
            TITLE,
            f"Mapa dos shinies previstos para **{player['trainer_name']}**:\n"
            f"<{Config.SHINY_MAP_URL.rstrip('/')}/{token}>\n"
            "-# O link é a chave: quem o tiver vê o mapa. Um link novo desliga este.",
        )

    async def _send_map_link(self, ctx, name):
        player = await self.store.find_linked(name)
        if player is None:
            await self._say(
                ctx, f"Não encontrei nenhum jogador associado chamado `{name}`."
            )
            return
        await ctx.author.send(embed=await self._map_link_embed(player))
        await self._say(
            ctx, f"Enviei-te por DM o mapa de **{player['trainer_name']}**. 📬"
        )

    async def _open_channel(self, ctx, name):
        """A private channel for one linked player: only they (and whoever
        administers the server) see it. Their map link is pinned there and
        their shiny alerts arrive there instead of by DM."""
        guild = ctx.guild
        if guild is None:
            await self._say(ctx, "Pede o canal no servidor, não em DM.")
            return
        player = await self.store.find_linked(name)
        if player is None:
            await self._say(
                ctx, f"Não encontrei nenhum jogador associado chamado `{name}`."
            )
            return
        existing = player.get("channel_id") and guild.get_channel(player["channel_id"])
        if existing:
            await self._say(
                ctx, f"**{player['trainer_name']}** já tem canal: {existing.mention}"
            )
            return
        member = guild.get_member(player["discord_id"])
        if member is None:
            await self._say(ctx, f"**{player['trainer_name']}** não está no servidor.")
            return

        hidden = discord.PermissionOverwrite(view_channel=False)
        bot = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            embed_links=True,
            attach_files=True,
            manage_messages=True,
        )
        try:
            category = discord.utils.get(guild.categories, name=CHANNEL_CATEGORY)
            if category is None:
                category = await guild.create_category(
                    CHANNEL_CATEGORY,
                    overwrites={guild.default_role: hidden, guild.me: bot},
                )
            channel = await guild.create_text_channel(
                f"shiny-{member.name}",
                category=category,
                topic=f"Shinies previstos para {player['trainer_name']}.",
                overwrites={
                    guild.default_role: hidden,
                    guild.me: bot,
                    # Read-only for the player: it is a feed, not a chat.
                    member: discord.PermissionOverwrite(
                        view_channel=True,
                        read_message_history=True,
                        send_messages=False,
                    ),
                },
            )
        except discord.Forbidden:
            await self._say(
                ctx,
                "Não tenho permissão para criar canais neste servidor (Gerir canais).",
            )
            return
        await self.store.set_channel(player["discord_id"], channel.id)
        link = await channel.send(embed=await self._map_link_embed(player))
        await link.pin()
        await self._say(
            ctx, f"Canal criado para **{player['trainer_name']}**: {channel.mention}"
        )

    @staticmethod
    def _status_text(row):
        if row is None:
            return (
                "Ainda não enviaste um código. Escreve `!shiny 1234 5678 9012` "
                "com o teu código de amigo."
            )
        if row["status"] == "pending":
            return f"O teu código está em fila. É associado {_WHEN}."
        if row["status"] == "linked":
            return f"Associado ao treinador **{row['trainer_name']}**."
        return (
            f"Não foi possível associar: {row['fail_reason']}. "
            "Envia `!shiny <código>` outra vez."
        )

    @staticmethod
    async def _say(ctx, text):
        await ctx.send(embed=build_embed(TITLE, text))


async def setup(poliswag):
    await poliswag.add_cog(Shiny(poliswag))
