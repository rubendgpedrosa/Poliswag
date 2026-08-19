import discord
from discord.ext import commands

from modules.config import Config

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
}

_NO_DESCRIPTION = "Sem descrição."


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

    def command_not_found(self, string):
        return f'Não encontrei nenhum comando chamado "{string}".'

    def subcommand_not_found(self, command, string):
        if isinstance(command, commands.Group) and len(command.all_commands) > 0:
            return (
                f'O comando "{command.qualified_name}" não tem nenhum '
                f'subcomando chamado "{string}".'
            )
        return f'O comando "{command.qualified_name}" não tem subcomandos.'

    async def send_bot_help(self, mapping):
        prefix = self.context.clean_prefix
        embed = discord.Embed(
            title="📖 Comandos disponíveis",
            description=f"Usa `{prefix}help <comando>` para mais detalhes sobre um comando específico.",
            color=Config.EMBED_COLOR,
        )
        for cog, cog_commands in mapping.items():
            filtered = await self.filter_commands(cog_commands, sort=True)
            if not filtered:
                continue
            lines = [
                f"`{prefix}{command.qualified_name}` — {command.brief or _NO_DESCRIPTION}"
                for command in filtered
            ]
            embed.add_field(
                name=self._cog_label(cog), value="\n".join(lines), inline=False
            )
        await self.get_destination().send(embed=embed)

    async def send_cog_help(self, cog):
        prefix = self.context.clean_prefix
        filtered = await self.filter_commands(cog.get_commands(), sort=True)
        embed = discord.Embed(title=self._cog_label(cog), color=Config.EMBED_COLOR)
        for command in filtered:
            embed.add_field(
                name=f"{prefix}{command.qualified_name}",
                value=command.help or command.brief or _NO_DESCRIPTION,
                inline=False,
            )
        await self.get_destination().send(embed=embed)

    async def send_group_help(self, group):
        prefix = self.context.clean_prefix
        embed = discord.Embed(
            title=f"📖 {prefix}{group.qualified_name}",
            description=group.help or group.brief or _NO_DESCRIPTION,
            color=Config.EMBED_COLOR,
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
        embed = discord.Embed(
            title=f"📖 {prefix}{command.qualified_name}",
            description=command.help or command.brief or _NO_DESCRIPTION,
            color=Config.EMBED_COLOR,
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
        embed = discord.Embed(title=f"❌ {error}", color=discord.Color.red())
        await self.get_destination().send(embed=embed)
