import discord
from discord.ext import commands
from modules.event_store import EventStore
from modules.embeds import build_excluded_list_embed
from modules.config import Config


class EventExclusion(commands.Cog):
    def __init__(self, poliswag):
        self.poliswag = poliswag
        self.event_store = EventStore(poliswag.db)

    async def cog_load(self):
        print(f"{self.__class__.__name__} loaded!")

    async def cog_unload(self):
        print(f"{self.__class__.__name__} unloaded!")

    def cog_check(self, ctx):
        return str(ctx.author.id) in self.poliswag.ADMIN_USERS_IDS

    @commands.command(
        name="exclude",
        brief="Exclui um tipo de evento de ser notificado.",
        help="Adiciona um tipo de evento à lista de exclusão. Utilização: exclude <TIPO_DE_EVENTO>",
    )
    async def exclude_event(self, ctx, *, event_type):
        event_type = event_type.lower()

        if self.event_store.is_excluded(event_type):
            await ctx.send(
                f"O tipo de evento '{event_type}' já está na lista de exclusão."
            )
            return

        self.event_store.add_excluded(event_type)

        confirm_embed = discord.Embed(
            title="Tipo de Evento Excluído",
            description=f"Eventos do tipo **{event_type}** adicionado à lista de exclusão.",
            color=Config.EMBED_COLOR,
        )
        await ctx.send(embed=confirm_embed)

        excluded_list_embed = await build_excluded_list_embed(
            self.poliswag.db,
            title="Lista de tipo de Eventos Excluídos",
            footer_text="Use !excludedlist para ver esta lista novamente",
        )
        await ctx.send(embed=excluded_list_embed)

    @commands.command(
        name="include",
        brief="Remove um tipo de evento das notificações.",
        help="Remove um tipo de evento da lista de exclusão. Utilização: include <TIPO_DE_EVENTO>",
    )
    async def include_event(self, ctx, *, event_type):
        event_type = event_type.lower()
        affected_rows = self.event_store.remove_excluded(event_type)

        if affected_rows == 0:
            await ctx.send(f"O tipo de evento '{event_type}' não estava excluído.")
        else:
            remove_embed = discord.Embed(
                title="Tipo de Evento Incluído",
                description=f"**{event_type}** voltou a ser incluído nas notificações.",
                color=Config.EMBED_COLOR,
            )
            await ctx.send(embed=remove_embed)

            excluded_list_embed = await build_excluded_list_embed(
                self.poliswag.db,
                title="Lista Atualizada de Tipos de Eventos Excluídos",
                footer_text="Use !excludedlist para ver esta lista novamente",
            )
            await ctx.send(embed=excluded_list_embed)

    @commands.command(
        name="excludeclear",
        brief="Limpa toda a lista de exclusão de eventos",
        help="Remove todos os tipos de eventos da lista de exclusão.",
    )
    async def exclude_clear_all_events(self, ctx):
        count = self.event_store.clear_excluded()

        confirm_embed = discord.Embed(
            title="Todos os Tipos de Eventos Incluídos",
            description=f"{count} tipos de eventos foram removidos da lista de exclusão.",
            color=Config.EMBED_COLOR,
        )
        await ctx.send(embed=confirm_embed)

    @commands.command(
        name="excludedlist",
        brief="Lista todos os tipos de eventos excluídos",
        help="Mostra uma lista de todos os tipos de eventos que estão excluídos.",
    )
    async def excluded_list(self, ctx):
        excluded_list_embed = await build_excluded_list_embed(self.poliswag.db)
        await ctx.send(embed=excluded_list_embed)

    @commands.command(
        name="eventtypes",
        brief="Lista todos os tipos de eventos existentes para fácil consulta.",
        help="Mostra uma lista de todos os tipos de eventos que estão registados na base de dados.",
    )
    async def event_types(self, ctx):
        event_types = self.event_store.get_all_event_types()

        if not event_types:
            await ctx.send("Não foram encontrados tipos de eventos.")
            return

        event_type_list = "\n".join(
            [f"- {event['event_type']}" for event in event_types]
        )

        embed = discord.Embed(
            title="Tipos de Eventos Registados",
            description=f"Lista de tipos de eventos:\n{event_type_list}",
            color=Config.EMBED_COLOR,
        )
        await ctx.send(embed=embed)


async def setup(poliswag):
    await poliswag.add_cog(EventExclusion(poliswag))
