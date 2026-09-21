import discord

from modules.config import Config
from modules.embeds import status_embed

# Edited per event: the channel mention below is the event channel the
# Eventos role unlocks. Nothing else needs changing -- the role is
# permanent and shared by every event.
_PANEL_TITLE = "🎉 Canal de eventos"
_PANEL_BODY = (
    "Carrega no botão para receberes o cargo **Eventos** e veres o canal "
    "do próximo evento: <#1551534974413443082>.\n\n"
    "Carrega outra vez para saíres e deixares de ver os canais de eventos."
)
_BUTTON_LABEL = "Quero participar"
_BUTTON_CUSTOM_ID = "event_panel:toggle"

_AUDIT_REASON = "Auto-atribuição via !eventpanel"

_JOINED = "✅ Já tens o cargo **Eventos** — o canal do evento aparece-te agora."
_LEFT = "👋 Removi-te o cargo **Eventos**. Carrega outra vez quando quiseres voltar."
_ERR_CONFIG = "⚠️ Configuração inválida — avisa um admin."
_ERR_NOT_MEMBER = "⚠️ Não te encontro no servidor. Entra lá primeiro."
_ERR_FAILED = "⚠️ Não consegui alterar o teu cargo. Já avisei os admins."


class EventPanelView(discord.ui.View):
    """Persistent view: timeout=None plus a fixed custom_id, so panels
    posted for past events keep working after every restart, provided
    main.py registers it with bot.add_view."""

    def __init__(self, poliswag):
        super().__init__(timeout=None)
        self.poliswag = poliswag

    @discord.ui.button(
        label=_BUTTON_LABEL,
        emoji="🎉",
        style=discord.ButtonStyle.success,
        custom_id=_BUTTON_CUSTOM_ID,
    )
    async def _toggle_button(self, interaction, button):
        await self.handle_click(interaction)

    def _guild(self, interaction):
        """In a DM interaction.guild is None, so fall back to the guild
        behind the panel channel -- the bot serves a single guild."""
        if interaction.guild is not None:
            return interaction.guild
        channel = getattr(self.poliswag, "EVENT_PANEL_CHANNEL", None)
        return channel.guild if channel is not None else None

    async def handle_click(self, interaction):
        guild = self._guild(interaction)
        role = guild.get_role(Config.EVENTS_ROLE_ID) if guild is not None else None
        if role is None:
            self.poliswag.utility.log_to_file(
                f"[EVENTPANEL] role {Config.EVENTS_ROLE_ID} not found", "ERROR"
            )
            await interaction.response.send_message(_ERR_CONFIG, ephemeral=True)
            return

        member = await self._member(guild, interaction.user.id)
        if member is None:
            await interaction.response.send_message(_ERR_NOT_MEMBER, ephemeral=True)
            return

        had_role = role in member.roles
        try:
            if had_role:
                await member.remove_roles(role, atomic=True, reason=_AUDIT_REASON)
            else:
                await member.add_roles(role, atomic=True, reason=_AUDIT_REASON)
        except discord.HTTPException as e:
            # Forbidden subclasses HTTPException: the usual cause is the
            # Eventos role sitting above Poliswag's own role.
            await self._report_failure(interaction, member, had_role, e)
            return

        await interaction.response.send_message(
            _LEFT if had_role else _JOINED, ephemeral=True
        )

    async def _member(self, guild, user_id):
        member = guild.get_member(user_id)
        if member is not None:
            return member
        try:
            return await guild.fetch_member(user_id)
        except discord.HTTPException:
            return None

    async def _report_failure(self, interaction, member, had_role, error):
        action = "remover" if had_role else "dar"
        self.poliswag.utility.log_to_file(
            f"[EVENTPANEL] Failed to {action} role for {member} ({member.id}): {error}",
            "ERROR",
        )
        await interaction.response.send_message(_ERR_FAILED, ephemeral=True)
        mod_channel = self.poliswag.MOD_CHANNEL
        if mod_channel is None:
            return
        embed = status_embed(
            "⚠️ Painel de eventos falhou",
            f"Não consegui {action} o cargo **Eventos** a {member.mention}.\n"
            f"`{error}`\n\nVerifica se o cargo está abaixo do cargo do Poliswag.",
            color=0xE74C3C,
        )
        await self.poliswag.utility.send_embed_to_channel(mod_channel, embed)
