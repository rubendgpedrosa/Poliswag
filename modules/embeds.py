import discord
from datetime import datetime
from modules.config import Config

_MAX_FIELDS = 25
_MAX_FIELD_NAME = 256
_MAX_FIELD_VALUE = 1024
_MAX_DESCRIPTION = 4096


def build_embed(title, description="", footer=None):
    embed = discord.Embed(
        title=title,
        description=description,
        color=Config.EMBED_COLOR,
        timestamp=datetime.now(),
    )
    if footer:
        embed.set_footer(text=footer)
    return embed


async def build_tracked_list_embed(db, title="Quests Seguidas", footer_text=None):
    tracked_quests = db.get_data_from_database(
        "SELECT target, creator, createddate FROM tracked_quest_reward ORDER BY createddate DESC"
    )

    if not tracked_quests:
        embed = discord.Embed(
            title=title,
            description="Não há quests/rewards a serem seguidas atualmente.",
            color=Config.EMBED_COLOR,
        )
    else:
        embed = discord.Embed(title=title, color=Config.EMBED_COLOR)
        for quest_data in tracked_quests[:_MAX_FIELDS]:
            createddate = quest_data.get("createddate", "Data desconhecida")
            createddate_str = (
                createddate.strftime("%d/%m/%Y %H:%M")
                if isinstance(createddate, datetime)
                else str(createddate)
            )
            name = quest_data["target"][:_MAX_FIELD_NAME]
            value = f"Adicionado por: {quest_data['creator']}\n{createddate_str}"
            embed.add_field(name=name, value=value[:_MAX_FIELD_VALUE], inline=False)

        if len(tracked_quests) > _MAX_FIELDS:
            embed.set_footer(
                text=f"A mostrar {_MAX_FIELDS} de {len(tracked_quests)} quests."
            )
            return embed

    if footer_text:
        embed.set_footer(text=footer_text)
    return embed


async def build_excluded_list_embed(
    db, title="Lista de Tipos de Eventos Excluídos", footer_text=None
):
    excluded_events = db.get_data_from_database("SELECT type FROM excluded_event_type")

    if not excluded_events:
        embed = discord.Embed(
            title=title,
            description="Não há tipos de eventos excluídos.",
            color=Config.EMBED_COLOR,
        )
    else:
        event_list = "\n".join([f"- {e['type']}" for e in excluded_events])
        if len(event_list) > _MAX_DESCRIPTION:
            event_list = event_list[: _MAX_DESCRIPTION - 20] + "\n... (truncado)"
        embed = discord.Embed(
            title=title,
            description=f"Tipos de eventos excluídos:\n{event_list}",
            color=Config.EMBED_COLOR,
        )

    if footer_text:
        embed.set_footer(text=footer_text)
    return embed


async def build_tracked_summary_embeds(
    channel, reward_groups, footer_text, ui_icons_url
):
    for reward_slug, group_data in reward_groups.items():
        count = len(group_data["pokestops"])
        embed = discord.Embed(color=Config.EMBED_COLOR)
        embed.description = f"**{count} quest de {group_data['title']}**"
        embed.set_footer(text=footer_text)
        embed.set_author(
            name=group_data.get("reward_text", ""),
            icon_url=f"{ui_icons_url}{reward_slug}",
        )
        await channel.send(embed=embed)
