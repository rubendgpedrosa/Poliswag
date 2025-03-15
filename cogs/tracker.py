import discord
from discord.ext import commands
from datetime import datetime


class Tracker(commands.Cog):
    def __init__(self, poliswag):
        self.poliswag = poliswag
        self.embed_color = 0x4169E1

    async def cog_load(self):
        print(f"{self.__class__.__name__} loaded!")

    async def cog_unload(self):
        print(f"{self.__class__.__name__} unloaded!")

    def cog_check(self, ctx):
        return str(ctx.author.id) in self.ADMIN_USERS_IDS

    @commands.command(
        name="track",
        brief="Adiciona uma quest à lista de seguimento",
        help="Adiciona uma quest à lista de quests a serem seguidas. Utilização: track <SUBSTRING_PESQUISA>",
    )
    async def track(self, ctx, *, search_string):
        search_string = search_string.lower()
        creator = str(ctx.author.name)

        existing_quest = self.poliswag.db.get_data_from_database(
            f"SELECT target FROM tracked_quest_reward WHERE target = '{search_string}'"
        )

        if len(existing_quest) > 0:
            await ctx.send(f"A quest '{search_string}' já está a ser seguida.")
            return

        current_time = datetime.now()
        self.poliswag.db.execute_query_to_database(
            f"INSERT INTO tracked_quest_reward (target, creator, createddate) VALUES ('{search_string}', '{creator}', '{current_time}')"
        )

        confirm_embed = discord.Embed(
            title="Quest Adicionada",
            description=f"Agora a seguir a quest: **{search_string}**",
            color=self.embed_color,
        )

        await ctx.send(embed=confirm_embed)

        tracked_list_embed = await self.poliswag.utility.build_tracked_list_embed(
            title="Lista Atualizada de Quests",
            footer_text="Use !tracklist para ver esta lista novamente",
        )
        await ctx.send(embed=tracked_list_embed)

    @commands.command(
        name="untrack",
        brief="Remove uma quest da lista de seguimento",
        help="Remove uma quest da lista de quests a serem seguidas. Utilização: untrack <SUBSTRING_PESQUISA>",
    )
    async def untrack(self, ctx, *, search_string):
        search_string = search_string.lower()

        quest_to_remove = self.poliswag.db.get_data_from_database(
            f"SELECT target FROM tracked_quest_reward WHERE target = '{search_string}'"
        )

        affected_rows = self.poliswag.db.execute_query_to_database(
            f"DELETE FROM tracked_quest_reward WHERE target = '{search_string}'"
        )

        if affected_rows == 0:
            await ctx.send(
                f"A quest '{search_string}' não está a ser seguida atualmente."
            )
        else:
            remove_embed = discord.Embed(
                title="Quest Removida",
                description=f"Deixou de seguir a quest: **{search_string}**",
                color=self.embed_color,
            )
            await ctx.send(embed=remove_embed)

            tracked_list_embed = await self.poliswag.utility.build_tracked_list_embed(
                title="Lista Atualizada de Quests",
                footer_text="Use !tracklist para ver esta lista novamente",
            )
            await ctx.send(embed=tracked_list_embed)

    @commands.command(
        name="untrackall",
        brief="Limpa toda a lista de seguimento",
        help="Remove todas as quests da lista.",
    )
    async def untrack_all(self, ctx):
        tracked_count = self.poliswag.db.get_data_from_database(
            "SELECT COUNT(*) as count FROM tracked_quest_reward"
        )
        count = tracked_count[0]["count"] if tracked_count else 0

        self.poliswag.db.execute_query_to_database("DELETE FROM tracked_quest_reward")

        confirm_embed = discord.Embed(
            title="Todas as Quests Removidas",
            description=f"{count} quests foram removidas da lista de seguimento.",
            color=self.embed_color,
        )

        await ctx.send(embed=confirm_embed)

    @commands.command(
        name="tracklist",
        aliases=["list"],
        brief="Lista todas as quests seguidas",
        help="Mostra uma lista de todas as quests que estão a ser seguidas.",
    )
    async def track_list(self, ctx):
        tracked_list_embed = await self.poliswag.utility.build_tracked_list_embed()
        await ctx.send(embed=tracked_list_embed)

    @commands.command(
        name="tracked",
        brief="Envia a lista de quests seguidas para o canal de convívio.",
        help="Verifica as quests que estão a ser seguidas e envia uma lista das encontradas para o canal convívio.",
    )
    async def check_tracked_by_cmd(self, ctx):
        await self.poliswag.quest_search.check_tracked(ctx)


async def setup(poliswag):
    await poliswag.add_cog(Tracker(poliswag))
