import discord
from discord.ext import commands
from modules.tracker_store import TrackerStore
from modules.embeds import build_tracked_list_embed
from modules.config import Config


class Tracker(commands.Cog):
    def __init__(self, poliswag):
        self.poliswag = poliswag
        self.tracker_store = TrackerStore(poliswag.db)

    async def cog_load(self):
        print(f"{self.__class__.__name__} loaded!")

    async def cog_unload(self):
        print(f"{self.__class__.__name__} unloaded!")

    def cog_check(self, ctx):
        return str(ctx.author.id) in self.poliswag.ADMIN_USERS_IDS

    @commands.command(
        name="track",
        brief="Adiciona uma quest à lista de seguimento",
        help="Adiciona uma quest à lista de quests a serem seguidas. Utilização: track <SUBSTRING_PESQUISA>",
    )
    async def track(self, ctx, *, search_string):
        search_string = search_string.lower()

        if self.tracker_store.exists(search_string):
            await ctx.send(f"A quest '{search_string}' já está a ser seguida.")
            return

        self.tracker_store.add(search_string, str(ctx.author.name))

        confirm_embed = discord.Embed(
            title="Quest Adicionada",
            description=f"Agora a seguir a quest: **{search_string}**",
            color=Config.EMBED_COLOR,
        )
        await ctx.send(embed=confirm_embed)

        tracked_list_embed = await build_tracked_list_embed(
            self.poliswag.db,
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
        affected_rows = self.tracker_store.remove(search_string)

        if affected_rows == 0:
            await ctx.send(
                f"A quest '{search_string}' não está a ser seguida atualmente."
            )
        else:
            remove_embed = discord.Embed(
                title="Quest Removida",
                description=f"Deixou de seguir a quest: **{search_string}**",
                color=Config.EMBED_COLOR,
            )
            await ctx.send(embed=remove_embed)

            tracked_list_embed = await build_tracked_list_embed(
                self.poliswag.db,
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
        count = self.tracker_store.clear()

        confirm_embed = discord.Embed(
            title="Todas as Quests Removidas",
            description=f"{count} quests foram removidas da lista de seguimento.",
            color=Config.EMBED_COLOR,
        )
        await ctx.send(embed=confirm_embed)

    @commands.command(
        name="tracklist",
        aliases=["list"],
        brief="Lista todas as quests seguidas",
        help="Mostra uma lista de todas as quests que estão a ser seguidas.",
    )
    async def track_list(self, ctx):
        tracked_list_embed = await build_tracked_list_embed(self.poliswag.db)
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
