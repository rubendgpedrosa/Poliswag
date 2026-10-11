"""Reconcile approved website accounts with their private shiny channels.

The website records approval and queues the trainer link. The scheduler
creates the channel on its next tick and updates its setup message as linking
advances. Held/off/left accounts have their existing channel hidden. Retried introductions reuse the stored message.
"""

import asyncio
from urllib.parse import quote

import discord

from modules.config import Config
from modules.embeds import build_embed
from modules.shiny_store import ShinyStore

CHANNEL_CATEGORY = "🌟 SHINY ALERTS 🌟"
ARCHIVE_CATEGORY = "Shinies (arquivo)"
CHANNEL_LOCK = asyncio.Lock()
# The pinned first message of a player's channel, above its map button.
# `!shiny canal` posts the same one.
STARTUP_TEXT = "Estamos a iniciar os teus alertas de shinies."
READY_TEXT = "Os teus alertas de shinies estão ativos."
PROCESSING_TEXT = "Já estamos a associar o teu treinador para preparar os teus alertas."
FAILED_TEXT = "Não foi possível iniciar os teus alertas. Confirma o teu Código de amizade no Pokédex e tenta associar novamente."
INTRO_TITLE = "✨ Notificações Pokémon Shiny"
INTRO_TEXT = (
    "Aqui irás receber as notificações dos Pokémon que a fórmula determina "
    "como shiny para a tua conta."
)


def enabled(row):
    return (
        int(row.get("shiny_access") or 0) == 1
        and int(row.get("shiny_map") or 0) == 1
        and row.get("left_at") is None
    )


def setup_phase(row):
    if row.get("status") == "linked" and row.get("player_id"):
        return "ready"
    if row.get("status") == "failed":
        return "failed"
    return "processing" if row.get("link_processing") else "pending"


def intro_embed(phase="ready"):
    text = INTRO_TEXT
    if phase == "pending":
        text = (
            "O teu pedido foi aprovado e está em fila para associarmos o treinador. "
            "Quando houver uma raid disponível, podes receber um convite no jogo: ignora-o. "
            "Atualizamos esta mensagem assim que começarmos."
        )
    elif phase == "processing":
        text = "Podes receber um convite de raid no jogo: ignora-o. Atualizamos esta mensagem quando os alertas estiverem prontos."
    elif phase == "failed":
        text = "Os alertas ficam à espera até conseguirmos associar o teu treinador."
    return build_embed(INTRO_TITLE, text)


def map_url(player):
    base = f"{Config.POKEDEX_URL.rstrip('/')}/mapa"
    name = player.get("trainer_name")
    return f"{base}/{quote(name, safe='')}" if name else base


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
        if member is not None:
            topics = {
                f"Shinies previstos para {name}."
                for name in (row.get("trainer_name"), member.name)
                if name
            }
            for channel in guild.text_channels:
                if (
                    channel.name == f"shiny-{member.name}"
                    and channel.topic in topics
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
            # Map-only accounts need no Discord channel. Existing channels
            # keep their map shortcut and the switch-off confirmation.
            if not row.get("shiny_notify_discord", 1):
                return channel if not row.get("channel_archived") else None
            category = await self._category(guild, CHANNEL_CATEGORY)
            overwrites = self._overwrites(guild, member, True)
            if channel is None:
                channel = await guild.create_text_channel(
                    f"shiny-{member.name}",
                    category=category,
                    topic=f"Shinies previstos para {row.get('trainer_name') or member.name}.",
                    overwrites=overwrites,
                )
            if not row.get("channel_id") or int(row["channel_id"]) != channel.id:
                await self.store.set_channel(discord_id, channel.id)
                row["channel_intro_message_id"] = None
                row["channel_intro_state"] = None
                row["channel_archived"] = 1
            if channel.category != category or channel.overwrites != overwrites:
                await channel.edit(category=category, overwrites=overwrites)
            # Approval can be withdrawn while the Discord requests run.
            latest = await self.store.channel_players(discord_id)
            if not latest or not enabled(latest[0]):
                await self._archive(guild, row, member, channel)
                return None
            # Use the latest linker state, rather than the snapshot before create.
            row.update(latest[0])
            if not row.get("shiny_notify_discord", 1):
                await self.store.set_channel_archived(discord_id, False)
                return channel
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
        phase = setup_phase(row)
        if (
            message_id
            and row.get("channel_intro_state") == phase
            and not row.get("channel_archived")
        ):
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
        view = discord.ui.View(timeout=None)
        view.add_item(discord.ui.Button(label="Ver mapa de shinies", url=map_url(row)))
        content = {
            "pending": STARTUP_TEXT,
            "processing": PROCESSING_TEXT,
            "failed": FAILED_TEXT,
            "ready": READY_TEXT,
        }[phase]
        shown_phase = phase
        if message is None:
            # Let a new, already-linked channel show the startup line first;
            # the following tick edits it to the accurate ready state.
            if phase == "ready":
                shown_phase = "starting"
            message = await channel.send(
                content=STARTUP_TEXT if phase == "ready" else content,
                embed=intro_embed(phase),
                view=view,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        elif row.get("channel_intro_state") != phase:
            await message.edit(
                content=content,
                embed=intro_embed(phase),
                view=view,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        await self.store.set_channel_intro(row["discord_id"], message.id)
        if not message.pinned:
            await message.pin()
        await self.store.set_channel_intro_state(row["discord_id"], shown_phase)
