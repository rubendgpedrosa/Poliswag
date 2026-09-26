import io

import discord
from discord.ext import commands

from modules.config import Config
from modules.embeds import status_embed

_IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".webp")
# What a bot may upload per file without a server boost.
_MAX_REUPLOAD_BYTES = 10 * 1024 * 1024

# Bots that hit the trap channel almost always blasted the same spam into
# every other channel too. A ban (unlike a kick) can purge a member's recent
# messages server-wide in the same API call -- this is how long a lookback
# that purge covers.
_TRAP_BAN_PURGE_SECONDS = 86400
_TRAP_REJOIN_INVITE = "https://discord.gg/pASCYbp"


def _build_trap_warning_embed(count):
    return status_embed(
        "🚫 NÃO ENVIEM MENSAGENS NESTE CANAL",
        "Este canal é usado para apanhar spam bots. Qualquer mensagem "
        "enviada aqui resulta numa **expulsão automática** e na limpeza das "
        "mensagens das últimas **24 horas**, em todo o servidor. "
        "O ban é removido logo de seguida; poderás voltar com um convite.\n\n"
        f"**Remoções efetuadas até agora:** {count}",
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
        self._trap_ban_count = 0

    async def cog_load(self):
        print(f"{self.__class__.__name__} loaded!")
        self._trap_ban_count = await self._load_trap_ban_count()
        await self._ensure_trap_warning_posted()

    async def cog_unload(self):
        print(f"{self.__class__.__name__} unloaded!")

    async def _load_trap_ban_count(self):
        try:
            rows = await self.poliswag.db.get_data_from_database(
                "SELECT trap_ban_count FROM poliswag"
            )
            if rows and rows[0]["trap_ban_count"] is not None:
                return rows[0]["trap_ban_count"]
        except Exception as e:
            self.poliswag.utility.log_to_file(f"Failed to load trap_ban_count: {e}")
        return 0

    async def _save_trap_ban_count(self, count):
        await self.poliswag.db.execute_query_to_database(
            "UPDATE poliswag SET trap_ban_count = %s", params=(count,)
        )

    async def _get_or_create_trap_message(self, channel):
        if self._trap_message is not None:
            return self._trap_message
        async for message in channel.history(limit=50):
            if message.author == self.poliswag.user:
                self._trap_message = message
                return message
        self._trap_message = await channel.send(
            embed=_build_trap_warning_embed(self._trap_ban_count)
        )
        return self._trap_message

    async def _refresh_trap_message(self, channel):
        """Ensure the warning/counter message in `channel` exists and
        reflects the current ban count. Self-heals if the message was
        deleted out from under us (discord.NotFound) by clearing the cached
        reference and reposting, instead of silently failing to update it
        forever until the next restart."""
        try:
            trap_message = await self._get_or_create_trap_message(channel)
            await trap_message.edit(
                content=None, embed=_build_trap_warning_embed(self._trap_ban_count)
            )
        except discord.NotFound:
            self._trap_message = None
            try:
                await self._get_or_create_trap_message(channel)
            except discord.HTTPException as e:
                self.poliswag.utility.log_to_file(
                    f"[TRAP] Failed to recreate warning message: {e}", "ERROR"
                )
        except discord.HTTPException as e:
            self.poliswag.utility.log_to_file(
                f"[TRAP] Failed to update warning message: {e}", "ERROR"
            )

    async def _ensure_trap_warning_posted(self):
        """Post (or find) the warning message at startup rather than waiting
        for the first violation -- the whole point is people see it *before*
        they get banned. Fetches the channel directly instead of waiting on
        poliswag.TRAP_CHANNEL, which is only resolved later in on_ready.

        Also re-edits an already-existing message to the current embed, so a
        wording/count change actually takes effect on deploy instead of only
        refreshing whenever the next ban happens to fire."""
        if not Config.TRAP_CHANNEL_ID:
            return
        try:
            trap_channel = await self.poliswag.fetch_channel(Config.TRAP_CHANNEL_ID)
        except discord.HTTPException as e:
            self.poliswag.utility.log_to_file(
                f"[TRAP] Failed to fetch trap channel: {e}", "ERROR"
            )
            return
        await self._refresh_trap_message(trap_channel)

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
        trap = self.poliswag.TRAP_CHANNEL
        if (
            mod_channel is None
            or quest_channel is None
            or message.channel.id in [mod_channel.id, quest_channel.id]
            # Trap deletions get their own report from on_message.
            or (trap is not None and message.channel.id == trap.id)
            or str(message.author.id) in self.poliswag.ADMIN_USERS_IDS
            or message.author == self.poliswag.user
        ):
            return

        # Commands like !trades delete their own invocation (the code must not
        # sit beside a visible !trades). That is the bot tidying up, not a
        # removal worth reporting. A bare @Poliswag (answered like !help) is
        # no valid command, so it needs its own check.
        if (
            await self.poliswag.get_context(message)
        ).valid or self.poliswag._is_bare_mention(message):
            return

        embed = status_embed(f"[{message.channel}] Mensagem removida", color=0x7B83B4)
        embed.add_field(
            name=str(message.author),
            value=message.content or "*(sem texto)*",
            inline=False,
        )

        # A deleted message's attachment URLs stop serving almost at once, so
        # linking them shows a broken image. Read each from Discord's media
        # cache (proxy_url) while it still answers and upload our own copy.
        files, lost = [], []
        for attachment in message.attachments:
            if attachment.size > _MAX_REUPLOAD_BYTES:
                lost.append(attachment)
                continue
            try:
                data = await attachment.read(use_cached=True)
            except discord.HTTPException:
                lost.append(attachment)
                continue
            files.append(
                (
                    attachment,
                    discord.File(io.BytesIO(data), filename=attachment.filename),
                )
            )

        image = next((f for a, f in files if _is_image_attachment(a)), None)
        if image:
            embed.set_image(url=f"attachment://{image.filename}")

        others = [f"📎 {f.filename}" for _, f in files if f is not image]
        others += [f"❌ {a.filename} (não recuperado)" for a in lost]
        if others:
            embed.add_field(name="Anexos", value="\n".join(others)[:1024], inline=False)

        await self.poliswag.utility.send_embed_to_channel(
            mod_channel, embed, files=[f for _, f in files]
        )

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

        # Send while still sharing the guild; after removal Discord may no
        # longer allow a DM. Closed DMs must never prevent the cleanup.
        invite_sent = False
        try:
            await message.author.send(
                "Enviaste uma mensagem no canal **#ignorar-este-canal**. "
                "Este canal aplica uma remoção automática e limpa as mensagens "
                "das últimas 24 horas no servidor. O ban é removido logo a seguir.\n\n"
                f"Para voltares ao PoGoLeiria, usa este convite: {_TRAP_REJOIN_INVITE}",
                allowed_mentions=discord.AllowedMentions.none(),
            )
            invite_sent = True
        except discord.HTTPException as e:
            self.poliswag.utility.log_to_file(
                f"[TRAP] Could not DM rejoin invite to {message.author} "
                f"({message.author.id}): {e}",
                "ERROR",
            )

        banned = False
        try:
            await message.author.ban(
                reason="Trap channel: remove member and purge previous 24 hours before unban",
                delete_message_seconds=_TRAP_BAN_PURGE_SECONDS,
            )
            banned = True
        except discord.HTTPException as e:
            self.poliswag.utility.log_to_file(
                f"[TRAP] Failed to ban {message.author} ({message.author.id}): {e}",
                "ERROR",
            )

        unbanned = False
        if banned:
            # Unban before counter/database/message work: unrelated failures
            # must not turn the requested removal into a permanent ban.
            try:
                await message.guild.unban(
                    message.author,
                    reason="Trap channel cleanup complete: allow rejoining by invite",
                )
                unbanned = True
            except discord.HTTPException as e:
                # Another concurrent trap message/moderator may have already
                # removed this ban. Other 404s are not proof of an unban.
                if isinstance(e, discord.NotFound) and e.code == 10026:
                    unbanned = True
                else:
                    self.poliswag.utility.log_to_file(
                        f"[TRAP] Ban succeeded but unban failed for {message.author} "
                        f"({message.author.id}); manual unban required: {e}",
                        "ERROR",
                    )
            self._trap_ban_count += 1
            try:
                await self._save_trap_ban_count(self._trap_ban_count)
            except Exception as e:
                self.poliswag.utility.log_to_file(
                    f"[TRAP] Failed to save removal counter: {e}", "ERROR"
                )

        await self._refresh_trap_message(trap_channel)

        if self.poliswag.MOD_CHANNEL is not None:
            embed = status_embed("🍯 Alguém caiu no canal-armadilha", color=0xE74C3C)
            unban_status = (
                "✅ removido; pode voltar com um convite"
                if unbanned
                else (
                    "❌ falhou; continua banido — remover manualmente"
                    if banned
                    else "não tentado (o ban falhou)"
                )
            )
            embed.add_field(
                name=str(message.author),
                value=(
                    f"**Ban:** {'✅ efectuado (mensagens dos últimos 24h purgadas)' if banned else '❌ falhou (ver logs)'}\n"
                    f"**Desban:** {unban_status}\n"
                    f"**Convite por DM:** {'✅ enviado' if invite_sent else '❌ não enviado (DMs fechadas ou erro)'}\n"
                    f"{message.content or '*(sem texto)*'}"
                )[:1024],
                inline=False,
            )
            await self.poliswag.utility.send_embed_to_channel(
                self.poliswag.MOD_CHANNEL, embed
            )


async def setup(poliswag):
    await poliswag.add_cog(Moderation(poliswag))
