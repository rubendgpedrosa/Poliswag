import json

import discord
from discord.ext import commands

from modules.config import Config
from modules.database_connector import DatabaseConnector
from modules.poracle_client import PoracleError


class Notifications(commands.Cog):
    """Moderator-only commands for managing per-channel Poracle pokemon alerts."""

    def __init__(self, poliswag):
        self.poliswag = poliswag
        self.poracle_db = DatabaseConnector("poracle")

    async def cog_load(self):
        print(f"{self.__class__.__name__} loaded!")

    async def cog_unload(self):
        print(f"{self.__class__.__name__} unloaded!")

    def cog_check(self, ctx):
        return str(ctx.author.id) in self.poliswag.ADMIN_USERS_IDS

    # ---- helpers --------------------------------------------------------

    def _pokemon_name(self, pokemon_id: int) -> str:
        name_map = self.poliswag.quest_search.pokemon_name_map or {}
        if not name_map:
            try:
                with open(Config.POKEMON_NAME_FILE, "r") as f:
                    name_map = json.load(f)
                self.poliswag.quest_search.pokemon_name_map = name_map
            except Exception:
                return f"#{pokemon_id}"
        return name_map.get(str(pokemon_id), f"#{pokemon_id}").title()

    def _resolve_name(self, name: str) -> int | None:
        matches = self.poliswag.quest_search.get_pokemon_id_by_pokemon_name_map(name)
        exact = [
            mid
            for mid in matches
            if self.poliswag.quest_search.pokemon_name_map.get(mid, "") == name.lower()
        ]
        if exact:
            return int(exact[0])
        if len(matches) == 1:
            return int(matches[0])
        return None

    # ---- command group --------------------------------------------------

    @commands.group(name="notify", invoke_without_command=True)
    async def notify(self, ctx):
        await ctx.send(
            "Subcomandos: `channels`, `list`, `add`, `remove`, `register`, `test`, `reload`"
        )

    @notify.command(name="channels", brief="Lista os canais registados em Poracle")
    async def channels_cmd(self, ctx):
        try:
            rows = self.poracle_db.get_data_from_database(
                "SELECT id, name, enabled FROM humans "
                "WHERE type = 'discord:channel' ORDER BY name"
            )
        except Exception as e:
            await ctx.send(f"Erro ao obter canais: {e}")
            return

        if not rows:
            await ctx.send("Sem canais registados em Poracle.")
            return

        lines = [
            f"{'🟢' if row['enabled'] else '🔴'} <#{row['id']}> — `{row['id']}` ({row['name']})"
            for row in rows
        ]
        embed = discord.Embed(
            title="Canais Poracle",
            description="\n".join(lines)[:4000],
            color=Config.EMBED_COLOR,
        )
        await ctx.send(embed=embed)

    @notify.command(name="list", brief="Lista pokémon seguidos num canal")
    async def list_cmd(self, ctx, channel: discord.TextChannel):
        try:
            rules = await self.poliswag.poracle.list_pokemon_tracking(channel.id)
        except PoracleError as e:
            await ctx.send(f"Erro ao obter regras: {e}")
            return

        if not rules:
            await ctx.send(f"Nenhum pokémon seguido em {channel.mention}.")
            return

        lines = []
        for r in rules:
            name = self._pokemon_name(r.get("pokemon_id", 0))
            iv = r.get("min_iv", 0)
            cp = r.get("min_cp", 0)
            uid = r.get("uid", "?")
            lines.append(f"`{uid}` {name} — IV≥{iv} CP≥{cp}")

        embed = discord.Embed(
            title=f"Pokémon seguidos em #{channel.name}",
            description="\n".join(lines)[:4000],
            color=Config.EMBED_COLOR,
        )
        embed.set_footer(text=f"Usa !notify remove #{channel.name} <uid> para remover")
        await ctx.send(embed=embed)

    @notify.command(name="add", brief="Adiciona um pokémon a um canal")
    async def add_cmd(
        self,
        ctx,
        channel: discord.TextChannel,
        name: str,
        min_iv: int = 0,
        min_cp: int = 0,
    ):
        pokemon_id = self._resolve_name(name)
        if pokemon_id is None:
            await ctx.send(
                f"Não encontrei um pokémon único para '{name}'. Tenta um nome exato."
            )
            return

        rule = {"pokemon_id": pokemon_id, "min_iv": min_iv, "min_cp": min_cp}
        try:
            await self.poliswag.poracle.add_pokemon_tracking(channel.id, rule)
            await self.poliswag.poracle.reload()
        except PoracleError as e:
            await ctx.send(f"Erro ao adicionar regra: {e}")
            return

        pretty = self._pokemon_name(pokemon_id)
        await ctx.send(
            f"✔ {pretty} (IV≥{min_iv}, CP≥{min_cp}) adicionado a {channel.mention}."
        )

    @notify.command(name="remove", brief="Remove uma regra pelo uid")
    async def remove_cmd(self, ctx, channel: discord.TextChannel, uid: str):
        try:
            await self.poliswag.poracle.delete_pokemon_tracking_uid(channel.id, uid)
            await self.poliswag.poracle.reload()
        except PoracleError as e:
            await ctx.send(f"Erro ao remover regra: {e}")
            return
        await ctx.send(f"✔ Regra `{uid}` removida de {channel.mention}.")

    @notify.command(name="register", brief="Regista um canal em Poracle")
    async def register_cmd(self, ctx, channel: discord.TextChannel):
        existing = await self.poliswag.poracle.get_human(channel.id)
        if existing:
            await ctx.send(f"{channel.mention} já está registado em Poracle.")
            return
        try:
            await self.poliswag.poracle.create_channel(channel.id, channel.name)
            await self.poliswag.poracle.start(channel.id)
        except PoracleError as e:
            await ctx.send(f"Erro ao registar canal: {e}")
            return
        await ctx.send(f"✔ {channel.mention} registado e activo.")

    @notify.command(name="reload", brief="Recarrega as regras em Poracle")
    async def reload_cmd(self, ctx):
        try:
            await self.poliswag.poracle.reload()
        except PoracleError as e:
            await ctx.send(f"Erro ao recarregar: {e}")
            return
        await ctx.send("✔ Regras recarregadas.")

    @notify.command(name="test", brief="Envia uma notificação de teste para o canal")
    async def test_cmd(self, ctx, channel: discord.TextChannel):
        webhook = {
            "pokemon_id": 25,
            "latitude": 39.744,
            "longitude": -8.807,
            "individual_attack": 15,
            "individual_defense": 15,
            "individual_stamina": 15,
            "cp": 999,
            "pokemon_level": 30,
        }
        target = {
            "id": str(channel.id),
            "name": channel.name,
            "type": "discord:channel",
            "language": "en",
            "template": "1",
        }
        try:
            await self.poliswag.poracle.test_pokemon(webhook, target)
        except PoracleError as e:
            await ctx.send(f"Erro ao enviar teste: {e}")
            return
        await ctx.send(f"✔ Teste enviado para {channel.mention}.")


async def setup(bot):
    await bot.add_cog(Notifications(bot))
