import discord
from discord.ext import commands

from modules.config import Config
from modules.embeds import status_embed

# Edited per event: put the event's name in below, then re-run
# !eventpanel to post a fresh message. A plain line rather than an embed,
# and it does its own @everyone -- the panel IS the announcement.
#
# It names no channel on purpose: the audience is precisely the people
# who cannot see it yet, and Discord renders a hidden channel's mention
# as a dead link for them.
#
# Every !eventpanel run pings the whole server, including a re-run to fix
# a typo. eventpanel_test sends the identical text with mentions
# suppressed, so a rehearsal can never fire it.
_PANEL_TEXT = (
    "@everyone\n"
    "Olá a todos! 🎉 Já temos um canal para o **<NOME DO EVENTO>**.\n\n"
    "Se quiseres juntar-te, carrega no botão aqui em baixo: recebes o "
    "cargo **Eventos** e o canal passa a aparecer-te. "
    "Carrega outra vez para saíres."
)
# Left in _PANEL_TEXT means the template was never filled in. Blocked
# on the live post only -- a rehearsal is exactly where you want to
# see it.
_PLACEHOLDER = "<NOME DO EVENTO>"
_BUTTON_LABEL = "Quero participar"
_BUTTON_CUSTOM_ID = "event_panel:toggle"

_AUDIT_REASON = "Auto-atribuição via !eventpanel"
_CLEAR_REASON = "Limpeza do cargo Eventos via !eventpanel clear"
# Enough to chase by hand; the log has the rest.
_FAILED_SHOWN = 10

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
        channel = self.poliswag.EVENT_PANEL_CHANNEL
        return channel.guild if channel is not None else None

    async def handle_click(self, interaction):
        # Ack first: resolving the member can cost an HTTP fetch and the
        # role change is another, which together can outrun Discord's 3s
        # deadline and show a red "interaction failed" on a click that
        # actually worked.
        await interaction.response.defer(ephemeral=True)

        guild = self._guild(interaction)
        role = guild.get_role(Config.EVENTS_ROLE_ID) if guild is not None else None
        if role is None:
            self.poliswag.utility.log_to_file(
                f"[EVENTPANEL] role {Config.EVENTS_ROLE_ID} not found", "ERROR"
            )
            await interaction.followup.send(_ERR_CONFIG, ephemeral=True)
            return

        member = await self._member(guild, interaction.user.id)
        if member is None:
            await interaction.followup.send(_ERR_NOT_MEMBER, ephemeral=True)
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

        await interaction.followup.send(_LEFT if had_role else _JOINED, ephemeral=True)

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
        await interaction.followup.send(_ERR_FAILED, ephemeral=True)
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


class EventPanel(commands.Cog):
    def __init__(self, poliswag):
        self.poliswag = poliswag

    async def cog_load(self):
        print(f"{self.__class__.__name__} loaded!")

    async def cog_unload(self):
        print(f"{self.__class__.__name__} unloaded!")

    def cog_check(self, ctx):
        return str(ctx.author.id) in self.poliswag.ADMIN_USERS_IDS

    def _preflight(self):
        """Returns (role, channel, error). A role that outranks Poliswag is
        the failure that would otherwise surface much later, as a Forbidden
        for every single member who clicks."""
        channel = self.poliswag.EVENT_PANEL_CHANNEL
        if channel is None:
            return (
                None,
                None,
                (
                    "EVENT_PANEL_CHANNEL_ID não está definido ou o canal não foi "
                    "encontrado."
                ),
            )
        role = channel.guild.get_role(Config.EVENTS_ROLE_ID)
        if role is None:
            return (
                None,
                None,
                (
                    f"Não existe nenhum cargo com o id `{Config.EVENTS_ROLE_ID}`. "
                    "Recria o cargo **Eventos** e actualiza `EVENTS_ROLE_ID` no `.env`."
                ),
            )
        if role.position >= channel.guild.me.top_role.position:
            return (
                None,
                None,
                (
                    f"O cargo **{role.name}** está acima (ou ao nível) do cargo do "
                    "Poliswag, por isso o bot não o consegue atribuir. Arrasta-o "
                    "para baixo nas definições de cargos."
                ),
            )
        return role, channel, None

    @commands.group(
        name="eventpanel",
        invoke_without_command=True,
        brief="Publica o painel de auto-atribuição do cargo Eventos",
        help=(
            "Publica no canal de anúncios uma mensagem com um botão que dá "
            "(ou tira) o cargo **Eventos**, o cargo que abre o canal do "
            "evento actual. Cada vez que corres o comando é publicada uma "
            "mensagem nova; o texto está em `cogs/event_panel.py`.\n\n"
            "`!eventpanel test` — envia-te o painel por DM para experimentares\n"
            "`!eventpanel clear` — tira o cargo a toda a gente"
        ),
    )
    async def eventpanel(self, ctx):
        role, channel, error = self._preflight()
        if error:
            await ctx.send(embed=status_embed("❌ Não publiquei o painel", error))
            return
        if _PLACEHOLDER in _PANEL_TEXT:
            await ctx.send(
                embed=status_embed(
                    "❌ Não publiquei o painel",
                    f"O texto ainda tem `{_PLACEHOLDER}`. Escreve o nome do "
                    "evento em `_PANEL_TEXT` (`cogs/event_panel.py`) antes de "
                    "publicar — isto ia com **@everyone** para toda a gente.",
                )
            )
            return
        try:
            await channel.send(
                content=_PANEL_TEXT,
                view=EventPanelView(self.poliswag),
                allowed_mentions=discord.AllowedMentions(everyone=True),
            )
        except discord.HTTPException as e:
            self.poliswag.utility.log_to_file(
                f"[EVENTPANEL] Failed to post the panel: {e}", "ERROR"
            )
            await ctx.send(
                embed=status_embed(
                    "❌ Não publiquei o painel",
                    f"O Discord recusou: `{e}`\n\nVerifica se o Poliswag pode "
                    f"escrever em {channel.mention}.",
                )
            )
            return
        await ctx.send(
            embed=status_embed(
                "✅ Painel publicado",
                f"O botão dá o cargo **{role.name}** em {channel.mention}.\n"
                "📣 Foi com **@everyone** — cada vez que corres o comando, "
                "toda a gente leva ping outra vez.",
            )
        )

    @eventpanel.command(
        name="test",
        brief="Envia-te o painel por DM",
        help=(
            "Envia-te o painel por mensagem privada para veres como fica. O "
            "botão funciona a sério: carrega nele e o cargo é mesmo "
            "atribuído no servidor."
        ),
    )
    async def eventpanel_test(self, ctx):
        _role_obj, _channel, error = self._preflight()
        if error:
            await ctx.send(embed=status_embed("❌ Não enviei o painel", error))
            return
        try:
            await ctx.author.send(
                content=_PANEL_TEXT,
                view=EventPanelView(self.poliswag),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        # Deliberately narrower than eventpanel's HTTPException: closed
        # DMs are the one expected failure here, and anything else is a
        # bug worth seeing rather than swallowing.
        except discord.Forbidden:
            await ctx.send(
                embed=status_embed(
                    "❌ Não te consigo enviar DM",
                    "Abre as mensagens privadas do servidor e tenta outra vez.",
                )
            )
            return
        await ctx.send(
            embed=status_embed(
                "📨 Enviado por DM",
                "O botão funciona a sério — o cargo é mesmo atribuído.",
            )
        )

    async def _sweep(self, holders, role):
        """Removes the role from everyone, counting instead of raising:
        one awkward member must not abort the rest of the sweep. Returns
        (removed, failed_members)."""
        removed = 0
        failed = []
        for member in holders:
            try:
                await member.remove_roles(role, atomic=True, reason=_CLEAR_REASON)
                removed += 1
            except discord.HTTPException as e:
                failed.append(member)
                self.poliswag.utility.log_to_file(
                    f"[EVENTPANEL] Failed to clear role from {member} "
                    f"({member.id}): {e}",
                    "ERROR",
                )
        return removed, failed

    @eventpanel.command(
        name="clear",
        brief="Tira o cargo Eventos a toda a gente",
        help=(
            "Remove o cargo **Eventos** a todos os membros que o têm, para "
            "limpar a lista no fim de um evento. Sem argumento só conta "
            "quantas pessoas seriam afectadas; `!eventpanel clear confirm` "
            "é que remove mesmo."
        ),
    )
    async def eventpanel_clear(self, ctx, confirm: str | None = None):
        role, channel, error = self._preflight()
        if error:
            await ctx.send(embed=status_embed("❌ Não limpei nada", error))
            return

        holders = [m for m in channel.guild.members if role in m.roles]
        if not holders:
            await ctx.send(
                embed=status_embed(
                    "Nada a limpar", f"Ninguém tem o cargo **{role.name}**."
                )
            )
            return

        if confirm != "confirm":
            await ctx.send(
                embed=status_embed(
                    "⚠️ Confirmação necessária",
                    f"**{len(holders)}** pessoas têm o cargo **{role.name}**.\n"
                    "Corre `!eventpanel clear confirm` para lhes tirar o cargo.",
                )
            )
            return

        async with ctx.typing():
            removed, failed = await self._sweep(holders, role)

        description = f"Cargo **{role.name}** removido a **{removed}** pessoas."
        if failed:
            names = ", ".join(m.mention for m in failed[:_FAILED_SHOWN])
            if len(failed) > _FAILED_SHOWN:
                names += f" (+{len(failed) - _FAILED_SHOWN})"
            description += f"\n⚠️ Falhou em **{len(failed)}**: {names}"
        await ctx.send(
            embed=status_embed(
                "🧹 Lista limpa",
                description,
                color=0xE74C3C if failed else None,
            )
        )


async def setup(poliswag):
    await poliswag.add_cog(EventPanel(poliswag))
