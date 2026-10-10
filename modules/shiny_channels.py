"""Reconcile approved website accounts with their private shiny channels.

The website records approval and queues the trainer link. Once the linker
resolves it, the scheduler creates the channel; held/off/left accounts have
their existing channel hidden. Retried introductions reuse the stored message.
"""

import asyncio
from urllib.parse import quote

import discord

from modules.config import Config
from modules.embeds import build_embed
from modules.shiny_store import ShinyStore

CHANNEL_CATEGORY = "Shinies"
ARCHIVE_CATEGORY = "Shinies (arquivo)"
CHANNEL_LOCK = asyncio.Lock()


def enabled(row):
    return (
        int(row.get("shiny_access") or 0) == 1
        and int(row.get("shiny_map") or 0) == 1
        and row.get("left_at") is None
        and row.get("status") == "linked"
        and bool(row.get("player_id"))
    )


def map_url(player):
    name = quote(player["trainer_name"], safe="")
    return f"{Config.POKEDEX_URL.rstrip('/')}/mapa/{name}"


class ShinyChannels:
    def __init__(self, poliswag, store=None):
        self.poliswag = poliswag
        self.store = store or ShinyStore(poliswag.db)

    async def tick(self):
        for row in await self.store.channel_players():
            try:
                guild = next(
                    (
                        g
                        for g in self.poliswag.guilds
                        if g.get_member(int(row["discord_id"]))
                        or (
                            row.get("channel_id")
                            and g.get_channel(int(row["channel_id"]))
                        )
                    ),
                    None,
                )
                if guild is not None:
                    await asyncio.wait_for(
                        self.ensure(guild, row["discord_id"]), timeout=15
                    )
            except Exception as error:
                self.poliswag.utility.log_to_file(
                    f"Shiny channel {row['discord_id']} setup failed: {error}",
                    "ERROR",
                )

    @staticmethod
    def _overwrites(guild, member, active):
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild.me: discord.PermissionOverwrite(
                view_channel=True,
                read_message_history=True,
                send_messages=True,
                embed_links=True,
                attach_files=True,
                manage_messages=True,
                manage_channels=True,
            ),
        }
        if member is not None:
            overwrites[member] = discord.PermissionOverwrite(
                view_channel=active,
                read_message_history=active,
                send_messages=False,
                send_messages_in_threads=False,
                create_public_threads=False,
                create_private_threads=False,
                add_reactions=False,
            )
        owner = guild.get_member(Config.MY_ID)
        if owner is not None:
            overwrites[owner] = discord.PermissionOverwrite(
                view_channel=True,
                read_message_history=True,
                send_messages=True,
                embed_links=True,
                attach_files=True,
                send_messages_in_threads=True,
                create_public_threads=True,
                create_private_threads=True,
                add_reactions=True,
            )
        return overwrites

    async def _category(self, guild, name):
        category = discord.utils.get(guild.categories, name=name)
        if category is None:
            category = await guild.create_category(
                name,
                position=len(guild.categories),
                overwrites=self._overwrites(guild, None, False),
            )
        return category

    async def _channel(self, guild, row, member):
        if row.get("channel_id"):
            channel_id = int(row["channel_id"])
            channel = guild.get_channel(channel_id)
            if channel is not None:
                return channel
            try:
                channel = await guild.fetch_channel(channel_id)
                return channel if channel.guild.id == guild.id else None
            except discord.NotFound:
                pass
        # Recover a successful Discord create whose database write failed.
        if member is not None and row.get("trainer_name"):
            topic = f"Shinies previstos para {row['trainer_name']}."
            for channel in guild.text_channels:
                if (
                    channel.name == f"shiny-{member.name}"
                    and channel.topic == topic
                    and channel.overwrites_for(member).view_channel is True
                ):
                    return channel
        return None

    async def ensure(self, guild, discord_id):
        async with CHANNEL_LOCK:
            rows = await self.store.channel_players(discord_id)
            if not rows:
                return None
            row = rows[0]
            member = guild.get_member(int(discord_id))
            channel = await self._channel(guild, row, member)
            if not enabled(row) or member is None:
                if channel is not None:
                    await self._archive(guild, row, member, channel)
                return None
            category = await self._category(guild, CHANNEL_CATEGORY)
            overwrites = self._overwrites(guild, member, True)
            if channel is None:
                channel = await guild.create_text_channel(
                    f"shiny-{member.name}",
                    category=category,
                    topic=f"Shinies previstos para {row['trainer_name']}.",
                    overwrites=overwrites,
                )
            if not row.get("channel_id") or int(row["channel_id"]) != channel.id:
                await self.store.set_channel(discord_id, channel.id)
                row["channel_intro_message_id"] = None
                row["channel_archived"] = 1
            if channel.category != category or channel.overwrites != overwrites:
                await channel.edit(category=category, overwrites=overwrites)
            # Approval can be withdrawn while the Discord requests run.
            latest = await self.store.channel_players(discord_id)
            if not latest or not enabled(latest[0]):
                await self._archive(guild, row, member, channel)
                return None
            await self._intro(guild, row, channel)
            latest = await self.store.channel_players(discord_id)
            if not latest or not enabled(latest[0]):
                await self._archive(guild, row, member, channel)
                return None
            await self.store.set_channel_archived(discord_id, False)
            return channel

    async def _archive(self, guild, row, member, channel):
        await self.store.set_channel_archived(row["discord_id"], True)
        category = await self._category(guild, ARCHIVE_CATEGORY)
        overwrites = self._overwrites(guild, member, False)
        if channel.category != category or channel.overwrites != overwrites:
            await channel.edit(category=category, overwrites=overwrites)

    async def _intro(self, guild, row, channel):
        message_id = row.get("channel_intro_message_id")
        if message_id and not row.get("channel_archived"):
            return
        message = None
        if message_id:
            try:
                message = await channel.fetch_message(int(message_id))
            except discord.NotFound:
                pass
        if message is None:
            # Adopt the map pin in channels created before automatic setup.
            for pin in await channel.pins():
                if pin.author.id == guild.me.id and any(
                    getattr(button, "url", None) == map_url(row)
                    for component in pin.components
                    for button in getattr(component, "children", [])
                ):
                    message = pin
                    break
        if message is None:
            view = discord.ui.View(timeout=None)
            view.add_item(
                discord.ui.Button(label="Ver mapa de shinies", url=map_url(row))
            )
            message = await channel.send(
                embed=build_embed(
                    "SHINY",
                    f"Shinies previstos para **{row['trainer_name']}**.\n"
                    "O mapa mostra todos os teus shinies previstos. Aqui recebes "
                    "apenas os que ainda te faltam na Pokédex.\n"
                    "Abre o mapa com a tua sessão da Pokédex.",
                ),
                view=view,
            )
        await self.store.set_channel_intro(row["discord_id"], message.id)
        if not message.pinned:
            await message.pin()
