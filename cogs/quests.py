import os
from discord.ext import commands


class Quests(commands.Cog):
    def __init__(self, poliswag):
        self.poliswag = poliswag
        self.SCAN_QUESTS_ALL_ENDPOINT = os.environ.get("SCAN_QUESTS_ALL_ENDPOINT")
        self.MAX_POKESTOPS_PER_EMBED = 10

    async def cog_load(self):
        print(f"{self.__class__.__name__} loaded!")

    async def cog_unload(self):
        print(f"{self.__class__.__name__} unloaded!")

    @commands.command(name="scan", brief="Inicia novo scan de quests")
    async def rescancmd(self, ctx):
        request = await self.poliswag.utility.fetch_data("scan_quest_all")
        if request.status_code == 200:
            await ctx.send("Scan de quests iniciado!")
            self.poliswag.scanner_manager.update_quest_scanning_state(0)
        else:
            await ctx.send("Erro ao iniciar o scan de quests!")

    @commands.command(
        name="questleiria",
        aliases=["questmarinha"],
        brief="Procura quests em Leiria ou Marinha",
        help="Pesquisa quests em Leiria ou Marinha com base na palavra-chave fornecida. Utilize: !questleiria <palavra-chave> ou !questmarinha <palavra-chave>",
    )
    async def questcmd(self, ctx):
        user = ctx.author
        search = (
            ctx.message.content.replace("!questleiria", "")
            .replace("!questmarinha", "")
            .strip()
        )

        if search == "":
            await ctx.send(f"{user.mention}, é necessário incluir algo para pesquisar!")
            return

        is_leiria = ctx.invoked_with == "questleiria"
        found_quests = self.poliswag.quest_search.find_quest_by_search_keyword(
            search.lower(), is_leiria
        )
        if not found_quests:
            await ctx.send(
                f"{user.mention}, não foram encontradas quests {'em Leiria' if is_leiria else 'na Marinha'} para '{search}'!"
            )
            return

        processing_msg = await ctx.send(
            f"{user.mention}, a processar resultados para '{search}'..."
        )
        reward_groups = self.poliswag.quest_search.group_pokestops_by_reward(
            found_quests
        )

        for reward_slug, group_data in reward_groups.items():
            reward_title = group_data["title"] + " - " + group_data["reward_text"]
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
