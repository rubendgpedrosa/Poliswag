import io

import discord
from discord.ext import commands

from modules.embeds import status_embed

# !anunciar <texto>: copies the text (and any attachments) of the command
# message into the announcements channel, exactly as typed -- line breaks,
# **bold**, <#channel> mentions and an @everyone included.
#
# Nothing goes out straight away. The command first echoes the post back
# where it was typed, with every mention suppressed, and asks Publicar /
# Cancelar: an @everyone can't be taken back, and a typo fixed by posting
# again pings the whole server a second time.

# Long enough to read the preview properly; after that the buttons die and
# nothing is posted.
_CONFIRM_TIMEOUT = 300
# What Discord accepts from a bot in one message.
_MAX_LENGTH = 2000

_USAGE = (
    "Escreve o anúncio a seguir ao comando, na mesma mensagem:\n"
    "```\n!anunciar Boas treinadores!\n\nTexto do anúncio…\n```\n"
    "Mudanças de linha, **negrito**, links e `@everyone` vão tal e qual. "
    "Imagens anexadas à mensagem também vão."
)
_NOT_AUTHOR = "Só quem escreveu o anúncio o pode publicar ou cancelar."


class AnnouncementConfirm(discord.ui.View):
    """Publicar / Cancelar under the preview. Only the author can press
    either, and the first press ends it, so a double click can't post
    twice."""

    def __init__(self, cog, ctx, text, attachments=()):
        super().__init__(timeout=_CONFIRM_TIMEOUT)
        self.cog = cog
        self.ctx = ctx
        self.text = text
        self.attachments = attachments
        self.message = None
        self.done = False

    async def interaction_check(self, interaction):
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message(_NOT_AUTHOR, ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Publicar", emoji="📣", style=discord.ButtonStyle.success)
    async def _publish_button(self, interaction, button):
        await self.handle_publish(interaction)

    @discord.ui.button(label="Cancelar", style=discord.ButtonStyle.secondary)
    async def _cancel_button(self, interaction, button):
        await self.handle_cancel(interaction)

    async def handle_publish(self, interaction):
        if self.done:
            return
        self.done = True
        self.stop()
        # Buttons off before the post, not after: the post can take a while
        # with attachments, and a second press in that gap must find nothing.
        await interaction.response.edit_message(view=None)
        embed = await self.cog.publish(self.ctx, self.text, self.attachments)
        await interaction.followup.send(embed=embed)

    async def handle_cancel(self, interaction):
        if self.done:
            return
        self.done = True
        self.stop()
        await interaction.response.edit_message(
            embed=status_embed("🚫 Anúncio cancelado", "Nada foi publicado."),
            view=None,
        )

    async def on_timeout(self):
        if self.done or self.message is None:
            return
        self.done = True
        try:
            await self.message.edit(
                embed=status_embed(
                    "⌛ Anúncio expirado",
                    "Passaram 5 minutos sem resposta. Nada foi publicado.",
                ),
                view=None,
            )
        except discord.HTTPException:
            pass


class Announcements(commands.Cog):
    def __init__(self, poliswag):
        self.poliswag = poliswag

    async def cog_load(self):
        print(f"{self.__class__.__name__} loaded!")

    async def cog_unload(self):
        print(f"{self.__class__.__name__} unloaded!")

    def cog_check(self, ctx):
        return str(ctx.author.id) in self.poliswag.ADMIN_USERS_IDS

    @commands.command(
        name="anunciar",
        brief="Publica um anúncio no canal de anúncios",
        help=(
            "Copia o texto escrito a seguir ao comando (e as imagens "
            "anexadas) para o canal de anúncios, tal e qual. Primeiro mostra "
            "como fica, sem pings, e só publica quando carregas em "
            "**Publicar** (tens 5 minutos).\n\n"
            "`!anunciar Boas treinadores! …`"
        ),
    )
    async def anunciar(self, ctx, *, texto: str = ""):
        channel = self.poliswag.EVENT_PANEL_CHANNEL
        if channel is None:
            await ctx.send(
                embed=status_embed(
                    "❌ Sem canal de anúncios",
                    "EVENT_PANEL_CHANNEL_ID não está definido ou o canal não "
                    "foi encontrado.",
                )
            )
            return
        text = texto.strip()
        if not text and not ctx.message.attachments:
            await ctx.send(embed=status_embed("📣 Como anunciar", _USAGE))
            return
        if len(text) > _MAX_LENGTH:
            await ctx.send(
                embed=status_embed(
                    "❌ Anúncio demasiado longo",
                    f"Tem {len(text)} caracteres; o Discord aceita até "
                    f"{_MAX_LENGTH} numa mensagem. Divide-o em dois.",
                )
            )
            return

        # Read once, now: the command message is deleted below, and its
        # attachments go with it.
        attachments = [
            (a.filename, await a.read(), a.is_spoiler())
            for a in ctx.message.attachments
        ]
        # The post exactly as it will look, pings off.
        await ctx.send(
            content=text or None,
            files=self._files(attachments),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        pings = "@everyone" in text or "@here" in text
        view = AnnouncementConfirm(self, ctx, text, attachments)
        view.message = await ctx.send(
            embed=status_embed(
                "📣 Publicar este anúncio?",
                f"Vai para {channel.mention} tal como está aqui em cima."
                + ("\n**Tem @everyone: toda a gente leva ping.**" if pings else ""),
            ),
            view=view,
        )
        # Only once the preview holds the text: on the errors above the
        # command stays, so a long announcement can be copied and fixed.
        if ctx.guild is not None:
            try:
                await ctx.message.delete()
            except discord.HTTPException:
                pass

    async def publish(self, ctx, text, attachments=()):
        """Posts the announcement; returns the embed that reports how it went."""
        channel = self.poliswag.EVENT_PANEL_CHANNEL
        try:
            message = await channel.send(
                content=text or None,
                files=self._files(attachments),
                # Written by an admin on purpose: every mention they typed
                # goes through, @everyone included.
                allowed_mentions=discord.AllowedMentions.all(),
            )
        except discord.HTTPException as e:
            self.poliswag.utility.log_to_file(
                f"[ANUNCIAR] Failed to post for {ctx.author} ({ctx.author.id}): {e}",
                "ERROR",
            )
            return status_embed(
                "❌ Não publiquei o anúncio",
                f"O Discord recusou: `{e}`\n\nVerifica se o Poliswag pode "
                f"escrever em {channel.mention}.",
            )
        self.poliswag.utility.log_to_file(
            f"[ANUNCIAR] {ctx.author} ({ctx.author.id}) posted {message.jump_url}"
        )
        return status_embed(
            "✅ Anúncio publicado",
            f"[Ver em {channel.mention}]({message.jump_url})",
        )

    @staticmethod
    def _files(attachments):
        """Fresh discord.Files for each send (one is consumed once sent),
        from the attachment bytes read when the command ran."""
        return [
            discord.File(io.BytesIO(data), filename=name, spoiler=spoiler)
            for name, data, spoiler in attachments
        ]


async def setup(poliswag):
    await poliswag.add_cog(Announcements(poliswag))
