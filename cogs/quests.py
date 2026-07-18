from discord.ext import commands


class Quests(commands.Cog):
    def __init__(self, poliswag):
        self.poliswag = poliswag
        self.MAX_POKESTOPS_PER_EMBED = 10

    async def cog_load(self):
        print(f"{self.__class__.__name__} loaded!")

    async def cog_unload(self):
        print(f"{self.__class__.__name__} unloaded!")

    @commands.command(name="exportquests", brief="Exporta quests para o PWA (admin)")
    async def exportquestscmd(self, ctx):
        if str(ctx.author.id) not in self.poliswag.ADMIN_USERS_IDS:
            return
        msg = await ctx.send("A exportar quests...")
        try:
            wrote = await self.poliswag.quest_exporter.export()
            await msg.edit(
                content=(
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
            await msg.edit(content=f"❌ Erro ao exportar quests: {e}")

    @commands.command(name="scan", brief="Inicia novo scan de quests")
    async def rescancmd(self, ctx):
        if str(ctx.author.id) not in self.poliswag.ADMIN_USERS_IDS:
            return
        from modules.http_client import fetch_data

        request = await fetch_data(
            "scan_quest_all", log_fn=self.poliswag.utility.log_to_file
        )
        if request is not None:
            await ctx.send("Scan de quests iniciado!")
            self.poliswag.scanner_manager.update_quest_scanning_state(0)
            self.poliswag.utility.log_to_file(
                f"[QUEST] @{ctx.author} ({ctx.author.id}): triggered quest scan"
            )
        else:
            await ctx.send("Erro ao iniciar o scan de quests!")

    @commands.command(
        name="questleiria",
        aliases=["questmarinha"],
        brief="Procura quests em Leiria ou Marinha",
        help="Pesquisa quests em Leiria ou Marinha com base na palavra-chave fornecida. Utilize: !questleiria <palavra-chave> ou !questmarinha <palavra-chave>",
    )
    async def questcmd(self, ctx, *, search=""):
        user = ctx.author
        search = search.strip()

        if not search:
            await ctx.send(f"{user.mention}, é necessário incluir algo para pesquisar!")
            return

        is_leiria = ctx.invoked_with == "questleiria"
        area = "Leiria" if is_leiria else "Marinha"
        found_quests = self.poliswag.quest_search.find_quest_by_search_keyword(
            search.lower(), is_leiria
        )
        if not found_quests:
            await ctx.send(
                f"{user.mention}, não foram encontradas quests {'em Leiria' if is_leiria else 'na Marinha'} para '{search}'!"
            )
            self.poliswag.utility.log_to_file(
                f"[QUEST] @{user} ({user.id}): searched '{search}' in {area} → 0 results"
            )
            return
        self.poliswag.utility.log_to_file(
            f"[QUEST] @{user} ({user.id}): searched '{search}' in {area} → found results"
        )

        processing_msg = await ctx.send(
            f"{user.mention}, a processar resultados para '{search}'..."
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
                    is_leiria,
                    page,
                    len(pokestop_groups),
                    total_stops=len(all_pokestops),
                )
                map_url = self.poliswag.image_generator.generate_static_map_for_group_of_quests(
                    pokestop_group
                )
                if map_url:
                    embed.set_image(url=map_url)
                await ctx.send(embed=embed)

        await processing_msg.delete()


async def setup(poliswag):
    await poliswag.add_cog(Quests(poliswag))
