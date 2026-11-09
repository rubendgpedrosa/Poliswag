from io import BytesIO

import discord
from discord.ext import commands

from modules.embeds import build_embed
from modules import scan_areas
from modules.permissions import mods_only


class Quests(commands.Cog):
    def __init__(self, poliswag):
        self.poliswag = poliswag
        self.MAX_POKESTOPS_PER_EMBED = 10

    async def cog_load(self):
        print(f"{self.__class__.__name__} loaded!")

    async def cog_unload(self):
        print(f"{self.__class__.__name__} unloaded!")

    @mods_only()
    @commands.command(name="exportquests", brief="Exporta quests para o PWA (admin)")
    async def exportquestscmd(self, ctx):
        msg = await ctx.send(embed=build_embed("⏳ A exportar quests..."))
        try:
            wrote = await self.poliswag.quest_exporter.export()
            await msg.edit(
                embed=build_embed(
                    "✅ Quests exportadas com sucesso!"
                    if wrote
                    else "✅ Sem alterações nas quests — nada para exportar."
                )
            )
            self.poliswag.utility.log_to_file(
                f"[QUEST] @{ctx.author} ({ctx.author.id}): "
                f"{'exported quests to PWA' if wrote else 'export skipped (no changes)'}"
            )
        except Exception as e:
            self.poliswag.utility.log_to_file(f"[QUEST] exportquestscmd failed: {e}")
            await msg.edit(embed=build_embed(f"❌ Erro ao exportar quests: {e}"))

    @mods_only()
    @commands.command(name="scan", brief="Inicia novo scan de quests")
    async def rescancmd(self, ctx):
        from modules.http_client import fetch_data

        request = await fetch_data(
            "scan_quest_all", log_fn=self.poliswag.utility.log_to_file
        )
        if request is not None:
            await ctx.send(embed=build_embed("✅ Scan de quests iniciado!"))
            await self.poliswag.scanner_manager.update_quest_scanning_state(0)
            self.poliswag.utility.log_to_file(
                f"[QUEST] @{ctx.author} ({ctx.author.id}): triggered quest scan"
            )
        else:
            await ctx.send(embed=build_embed("❌ Erro ao iniciar o scan de quests!"))

    @commands.command(
        name="quest",
        brief="Procura quests numa área",
        help=(
            "Pesquisa quests numa área com base na palavra-chave fornecida. "
            "Utilize: !quest <área> <palavra-chave>. As áreas ativas aparecem "
            "em !quest sem argumentos."
        ),
    )
    async def questcmd(self, ctx, area="", *, search=""):
        """Generic form: !quest <area> <keyword>."""
        user = ctx.author
        area = area.strip()
        await scan_areas.ensure()
        if not area:
            await ctx.send(embed=build_embed(self._areas_help(user)))
            return
        key = self._resolve_area(area)
        if key is None:
            await ctx.send(embed=build_embed(self._areas_help(user, area)))
            return
        await self._send_quests(ctx, key, search.strip())

    @commands.command(
        name="questleiria",
        aliases=["questmarinha"],
        brief="Procura quests numa área (forma antiga)",
        help=(
            "Pesquisa quests numa área com base na palavra-chave fornecida. "
            "Utilize: !questleiria <palavra-chave>, !questmarinha <palavra-chave> "
            "ou !quest <área> <palavra-chave>."
        ),
    )
    async def legacy_questcmd(self, ctx, *, search=""):
        """Legacy per-area names; the area key is the command name's suffix."""
        key = ctx.invoked_with.lower().removeprefix("quest")
        await self._send_quests(ctx, key, search.strip())

    def _resolve_area(self, value):
        wanted = value.strip().casefold()
        for row in scan_areas.active_areas():
            names = (
                row["area_key"],
                row["label"],
                row["dragonite_area"],
                row["poracle_area"],
            )
            if wanted in (name.strip().casefold() for name in names if name):
                return row["area_key"]
        return None

    def _areas_help(self, user, unknown=None):
        rows = scan_areas.active_areas()
        areas = " · ".join(f"**{row['area_key']}** ({row['label']})" for row in rows)
        if not rows:
            return f"{user.mention}, não há áreas ativas de momento."
        prefix = (
            f"{user.mention}, não conheço a área '{unknown}'."
            if unknown
            else f"{user.mention}, indica a área e a palavra-chave."
        )
        return f"{prefix} Áreas ativas: {areas}.\n" "Exemplo: `!quest leiria piplup`."

    async def _send_quests(self, ctx, area_key, search):
        user = ctx.author
        if not search:
            await ctx.send(
                embed=build_embed(
                    f"{user.mention}, é necessário incluir algo para pesquisar!"
                )
            )
            return
        if area_key not in scan_areas.active_keys():
            await ctx.send(embed=build_embed(self._areas_help(user)))
            return

        location = scan_areas.label(area_key)
        found_quests = await self.poliswag.quest_search.find_quest_by_search_keyword(
            search.lower(), area_key
        )
        if not found_quests:
            await ctx.send(
                embed=build_embed(
                    f"{user.mention}, não foram encontradas quests em {location} "
                    f"para '{search}'!"
                )
            )
            self.poliswag.utility.log_to_file(
                f"[QUEST] @{user} ({user.id}): searched '{search}' in {location} "
                "→ 0 results"
            )
            return
        self.poliswag.utility.log_to_file(
            f"[QUEST] @{user} ({user.id}): searched '{search}' in {location} "
            "→ found results"
        )

        processing_msg = await ctx.send(
            embed=build_embed(
                f"⏳ {user.mention}, a processar resultados para '{search}'..."
            )
        )
        reward_groups = self.poliswag.quest_search.group_pokestops_by_reward(
            found_quests
        )

        for reward_slug, group_data in reward_groups.items():
            reward_text = group_data.get("reward_text", "")
            reward_title = (
                f"{group_data['title']} — {reward_text}"
                if reward_text
                else group_data["title"]
            )
            all_pokestops = group_data["pokestops"]
            pokestop_groups = self.poliswag.quest_search.group_pokestops_geographically(
                all_pokestops, self.MAX_POKESTOPS_PER_EMBED
            )
            for page, pokestop_group in enumerate(pokestop_groups, 1):
                embed = self.poliswag.quest_search.create_quest_embed(
                    reward_title,
                    pokestop_group,
                    area_key,
                    page,
                    len(pokestop_groups),
                    total_stops=len(all_pokestops),
                )
                map_image = await self.poliswag.image_generator.generate_static_map_for_group_of_quests(
                    pokestop_group, area=area_key
                )
                if map_image:
                    filename = "quest-map.png"
                    embed.set_image(url=f"attachment://{filename}")
                    file = discord.File(BytesIO(map_image), filename=filename)
                    try:
                        await ctx.send(embed=embed, file=file)
                    finally:
                        file.close()
                else:
                    await ctx.send(embed=embed)

        await processing_msg.delete()


async def setup(poliswag):
    await poliswag.add_cog(Quests(poliswag))
