import discord
from discord.ext import commands

from modules.config import Config

_IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".webp")


def _build_trap_warning_embed(count):
    return discord.Embed(
        title="🚫 NÃO ENVIEM MENSAGENS NESTE CANAL",
        description=(
            "Este canal é usado para apanhar bots de spam. Qualquer mensagem "
            "enviada aqui resulta num **kick automático**.\n\n"
            f"**Pessoas expulsas até agora:** {count}"
        ),
        color=0xE74C3C,
    )


def _is_image_attachment(attachment):
    if attachment.content_type and attachment.content_type.startswith("image/"):
        return True
    return attachment.filename.lower().endswith(_IMAGE_EXTENSIONS)


class Moderation(commands.Cog):
    def __init__(self, poliswag):
        self.poliswag = poliswag
        # Cached reference to the warning/counter message in TRAP_CHANNEL --
        # avoids re-scanning channel history on every trap trigger.
        self._trap_message = None
        self._trap_kick_count = 0

    async def cog_load(self):
        print(f"{self.__class__.__name__} loaded!")
        self._trap_kick_count = await self._load_trap_kick_count()
        await self._ensure_trap_warning_posted()

    async def cog_unload(self):
        print(f"{self.__class__.__name__} unloaded!")

    async def _load_trap_kick_count(self):
        try:
            rows = await self.poliswag.db.get_data_from_database(
                "SELECT trap_kick_count FROM poliswag"
            )
            if rows and rows[0]["trap_kick_count"] is not None:
                return rows[0]["trap_kick_count"]
        except Exception as e:
            self.poliswag.utility.log_to_file(f"Failed to load trap_kick_count: {e}")
        return 0

    async def _save_trap_kick_count(self, count):
        await self.poliswag.db.execute_query_to_database(
            "UPDATE poliswag SET trap_kick_count = %s", params=(count,)
        )

    async def _get_or_create_trap_message(self, channel):
        if self._trap_message is not None:
            return self._trap_message
        async for message in channel.history(limit=50):
            if message.author == self.poliswag.user:
                self._trap_message = message
                return message
        self._trap_message = await channel.send(
            embed=_build_trap_warning_embed(self._trap_kick_count)
        )
        return self._trap_message

    async def _ensure_trap_warning_posted(self):
        """Post (or find) the warning message at startup rather than waiting
        for the first violation -- the whole point is people see it *before*
        they get kicked. Fetches the channel directly instead of waiting on
        poliswag.TRAP_CHANNEL, which is only resolved later in on_ready.

        Also re-edits an already-existing message to the current embed, so a
        wording/count change actually takes effect on deploy instead of only
        refreshing whenever the next kick happens to fire."""
        if not Config.TRAP_CHANNEL_ID:
            return
        try:
            trap_channel = await self.poliswag.fetch_channel(Config.TRAP_CHANNEL_ID)
            trap_message = await self._get_or_create_trap_message(trap_channel)
            await trap_message.edit(
                embed=_build_trap_warning_embed(self._trap_kick_count)
            )
        except discord.HTTPException as e:
            self.poliswag.utility.log_to_file(
                f"[TRAP] Failed to ensure warning message: {e}", "ERROR"
            )

    @commands.Cog.listener()
    async def on_interaction(self, interaction):
        if not interaction.data or "custom_id" not in interaction.data:
            return

        custom_id = interaction.data["custom_id"]
        if custom_id.startswith("Alertas") or custom_id in [
            "Leiria",
            "Marinha",
            "Remote",
            "Mystic",
            "Valor",
            "Instinct",
        ]:
            await self.poliswag.role_manager.response_user_role_selection(interaction)

    @commands.Cog.listener()
    async def on_message_delete(self, message):
        mod_channel = self.poliswag.MOD_CHANNEL
        quest_channel = self.poliswag.QUEST_CHANNEL
        if (
            mod_channel is None
            or quest_channel is None
            or message.channel.id in [mod_channel.id, quest_channel.id]
            or str(message.author.id) in self.poliswag.ADMIN_USERS_IDS
            or message.author == self.poliswag.user
        ):
            return

        embed = discord.Embed(
            title=f"[{message.channel}] Mensagem removida", color=0x7B83B4
        )
        embed.add_field(
            name=str(message.author),
            value=message.content or "*(sem texto)*",
            inline=False,
        )

        image_attachment = next(
            (a for a in message.attachments if _is_image_attachment(a)), None
        )
        if image_attachment:
            embed.set_image(url=image_attachment.url)

        other_attachments = [
            a for a in message.attachments if a is not image_attachment
        ]
        if other_attachments:
            embed.add_field(
                name="Anexos",
                value="\n".join(f"[{a.filename}]({a.url})" for a in other_attachments),
                inline=False,
            )

        await self.poliswag.utility.send_embed_to_channel(mod_channel, embed)

    @commands.Cog.listener()
    async def on_message(self, message):
        trap_channel = self.poliswag.TRAP_CHANNEL
        if (
            trap_channel is None
            or message.channel.id != trap_channel.id
            or message.author.bot
            or str(message.author.id) in self.poliswag.ADMIN_USERS_IDS
        ):
            return

        try:
            await message.delete()
        except discord.HTTPException as e:
            self.poliswag.utility.log_to_file(
                f"[TRAP] Failed to delete message from {message.author} "
                f"({message.author.id}): {e}",
                "ERROR",
            )

        kicked = False
        try:
            await message.author.kick(reason="Auto-kick: posted in trap channel")
            kicked = True
        except discord.HTTPException as e:
            self.poliswag.utility.log_to_file(
                f"[TRAP] Failed to kick {message.author} ({message.author.id}): {e}",
                "ERROR",
            )

        if kicked:
            self._trap_kick_count += 1
            await self._save_trap_kick_count(self._trap_kick_count)

        trap_message = await self._get_or_create_trap_message(trap_channel)
        try:
            await trap_message.edit(
                embed=_build_trap_warning_embed(self._trap_kick_count)
            )
        except discord.HTTPException as e:
            self.poliswag.utility.log_to_file(
                f"[TRAP] Failed to update counter message: {e}", "ERROR"
            )

        if self.poliswag.MOD_CHANNEL is not None:
            embed = discord.Embed(
                title="🍯 Alguém caiu no canal-armadilha",
                color=0xE74C3C,
            )
            embed.add_field(
                name=str(message.author),
                value=(
                    f"**Kick:** {'✅ efectuado' if kicked else '❌ falhou (ver logs)'}\n"
                    f"{message.content or '*(sem texto)*'}"
                ),
                inline=False,
            )
            await self.poliswag.utility.send_embed_to_channel(
                self.poliswag.MOD_CHANNEL, embed
            )


async def setup(poliswag):
    await poliswag.add_cog(Moderation(poliswag))
