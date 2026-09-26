import discord
from discord.ext import commands

from modules.embeds import status_embed
from modules.permissions import is_mod, is_owner

# Friendlier PT-PT category labels for !help, keyed by cog qualified_name.
# A cog not listed here falls back to its qualified_name as-is.
_COG_DISPLAY_NAMES = {
    "Quests": "🎯 Quests",
    "Accounts": "👤 Contas",
    "Tracker": "🔔 Tracking",
    "EventExclusion": "📅 Eventos",
    "ContainerManagerCog": "🖥️ Scanner (admin)",
    "Moderation": "🛡️ Moderação",
    "Notifications": "📣 Notificações",
    "Scheduled": "⏰ Agendados (admin)",
    "Lures": "🌸 Lures",
    "WebStats": "📊 Estatísticas (admin)",
    "Announcements": "📢 Anúncios (admin)",
}

_NO_DESCRIPTION = "Sem descrição."

# !help overview: three audiences. Mods (ADMIN_USERS_IDS) and MY_ID get
# compact name-only lines grouped by topic; `!help <comando>` has the detail.
# Cogs whose cog_check is MY_ID only; single commands use permissions.owner_only.
_OWNER_COGS = frozenset({"ContainerManagerCog", "WebStats", "EventPanel", "Accounts"})
# Aliases worth listing next to the command, for members who look for them.
_SHOWN_ALIASES = {"questleiria": ("questmarinha",)}
# Topic line for mod commands, keyed by cog qualified_name, in display order.
_MOD_TOPICS = {
    "Quests": "Quests",
    "Tracker": "Quests",
    "EventExclusion": "Eventos",
    "Scheduled": "Eventos",
    "Notifications": "Alertas",
    "Lures": "Lures",
    "Announcements": "Anúncios",
}


class EmbedHelpCommand(commands.HelpCommand):
    def __init__(self):
        super().__init__(
            command_attrs={
                "name": "help",
                "brief": "Mostra esta mensagem de ajuda",
                "help": (
                    "Mostra todos os comandos disponíveis, ou detalhes de um "
                    "comando ou categoria específica."
                ),
            }
        )

    def _cog_label(self, cog):
        if cog is None:
            return "Outros"
        return _COG_DISPLAY_NAMES.get(cog.qualified_name, cog.qualified_name)

    async def prepare_help_command(self, ctx, command=None):
        # Runs for !help, !help <comando> and a bare @Poliswag alike: the
        # command message goes, only the answer stays in the channel.
        await super().prepare_help_command(ctx, command)
        if ctx.guild is not None:
            try:
                await ctx.message.delete()
            except discord.HTTPException:
                pass

    def command_not_found(self, string):
        return f'Não encontrei nenhum comando chamado "{string}".'

    def subcommand_not_found(self, command, string):
        if isinstance(command, commands.Group) and len(command.all_commands) > 0:
            return (
                f'O comando "{command.qualified_name}" não tem nenhum '
                f'subcomando chamado "{string}".'
            )
        return f'O comando "{command.qualified_name}" não tem subcomandos.'

    def _audience(self, cog, command):
        """ "owner", "mods" or "all", from the same gates that refuse the
        command: an owner cog, a cog_check, or a permissions check on it."""
        if (cog and cog.qualified_name in _OWNER_COGS) or is_owner in command.checks:
            return "owner"
        has_cog_check = cog is not None and (
            type(cog).cog_check is not commands.Cog.cog_check
        )
        if has_cog_check or is_mod in command.checks:
            return "mods"
        return "all"

    async def send_bot_help(self, mapping):
        prefix = self.context.clean_prefix
        viewer_is_mod = is_mod(self.context)
        public, mods, owner = [], {}, []
        for cog, cog_commands in mapping.items():
            for command in await self.filter_commands(cog_commands, sort=True):
                audience = self._audience(cog, command)
                name = " ".join(
                    f"`{prefix}{n}`"
                    for n in (command.qualified_name,)
                    + _SHOWN_ALIASES.get(command.qualified_name, ())
                )
                if audience == "all":
                    public.append(f"{name} — {command.brief or _NO_DESCRIPTION}")
                elif audience == "owner":
                    owner.append(name)
                elif viewer_is_mod:
                    topic = _MOD_TOPICS.get(cog.qualified_name, "Outros")
                    mods.setdefault(topic, []).append(name)

        embed = status_embed(
            "📖 Comandos",
            f"`{prefix}help <comando>` mostra os detalhes de cada um.",
        )
        if public:
            embed.add_field(name="Para todos", value="\n".join(public), inline=False)
        if mods:
            order = list(dict.fromkeys(_MOD_TOPICS.values())) + ["Outros"]
            lines = [
                f"**{topic}:** {' '.join(mods[topic])}"
                for topic in order
                if topic in mods
            ]
            embed.add_field(name="🛡️ Mods", value="\n".join(lines), inline=False)
        if owner:
            embed.add_field(name="🔒 Só para ti", value=" ".join(owner), inline=False)
        await self.get_destination().send(embed=embed)

    async def send_cog_help(self, cog):
        prefix = self.context.clean_prefix
        filtered = await self.filter_commands(cog.get_commands(), sort=True)
        embed = status_embed(self._cog_label(cog))
        for command in filtered:
            embed.add_field(
                name=f"{prefix}{command.qualified_name}",
                value=command.help or command.brief or _NO_DESCRIPTION,
                inline=False,
            )
        await self.get_destination().send(embed=embed)

    async def send_group_help(self, group):
        prefix = self.context.clean_prefix
        embed = status_embed(
            f"📖 {prefix}{group.qualified_name}",
            group.help or group.brief or _NO_DESCRIPTION,
        )
        filtered = await self.filter_commands(group.commands, sort=True)
        for command in filtered:
            embed.add_field(
                name=f"{prefix}{command.qualified_name}",
                value=command.help or command.brief or _NO_DESCRIPTION,
                inline=False,
            )
        await self.get_destination().send(embed=embed)

    async def send_command_help(self, command):
        prefix = self.context.clean_prefix
        embed = status_embed(
            f"📖 {prefix}{command.qualified_name}",
            command.help or command.brief or _NO_DESCRIPTION,
        )
        if command.aliases:
            embed.add_field(
                name="Aliases", value=", ".join(command.aliases), inline=False
            )
        embed.add_field(
            name="Utilização",
            value=f"`{self.get_command_signature(command)}`",
            inline=False,
        )
        await self.get_destination().send(embed=embed)

    async def send_error_message(self, error):
        embed = status_embed(f"❌ {error}", color=discord.Color.red())
        await self.get_destination().send(embed=embed)
