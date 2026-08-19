"""Tests for modules.help_command.EmbedHelpCommand.

discord.py's HelpCommand base class does real permission filtering
(filter_commands) and command-signature formatting (get_command_signature)
against live Command objects, neither of which are worth reconstructing with
mocks -- both are stubbed per test so we can focus on the embed shape our
overrides build.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from modules.help_command import EmbedHelpCommand


def _fake_command(name, brief=None, help=None, aliases=None):
    cmd = MagicMock()
    cmd.qualified_name = name
    cmd.name = name
    cmd.brief = brief
    cmd.help = help
    cmd.aliases = aliases or []
    return cmd


def _fake_cog(qualified_name):
    cog = MagicMock()
    cog.qualified_name = qualified_name
    return cog


@pytest.fixture
def hc():
    help_command = EmbedHelpCommand()
    help_command.context = MagicMock()
    help_command.context.clean_prefix = "!"
    help_command.context.channel = MagicMock()
    help_command.context.channel.send = AsyncMock()
    return help_command


class TestCogLabel:
    def test_known_cog_uses_friendly_name(self, hc):
        assert hc._cog_label(_fake_cog("Quests")) == "🎯 Quests"

    def test_unknown_cog_falls_back_to_qualified_name(self, hc):
        assert hc._cog_label(_fake_cog("SomeNewCog")) == "SomeNewCog"

    def test_none_cog_is_outros(self, hc):
        assert hc._cog_label(None) == "Outros"


class TestSendBotHelp:
    async def test_builds_one_field_per_cog_with_commands(self, hc):
        track_cmd = _fake_command("track", brief="Segue uma quest")
        report_cmd = _fake_command("accounts", brief="Relatório de contas")
        mapping = {
            _fake_cog("Tracker"): [track_cmd],
            _fake_cog("Accounts"): [report_cmd],
        }
        hc.filter_commands = AsyncMock(side_effect=lambda cmds, **kw: list(cmds))

        await hc.send_bot_help(mapping)

        hc.context.channel.send.assert_awaited_once()
        embed = hc.context.channel.send.call_args.kwargs["embed"]
        assert len(embed.fields) == 2
        names = {f.name for f in embed.fields}
        assert "🔔 Tracking" in names
        assert "👤 Contas" in names
        tracker_field = next(f for f in embed.fields if f.name == "🔔 Tracking")
        assert "`!track` — Segue uma quest" in tracker_field.value

    async def test_cog_with_no_visible_commands_is_skipped(self, hc):
        mapping = {_fake_cog("Moderation"): [_fake_command("secret")]}
        hc.filter_commands = AsyncMock(return_value=[])

        await hc.send_bot_help(mapping)

        embed = hc.context.channel.send.call_args.kwargs["embed"]
        assert embed.fields == []

    async def test_missing_brief_shows_placeholder(self, hc):
        mapping = {_fake_cog("Quests"): [_fake_command("scan", brief=None)]}
        hc.filter_commands = AsyncMock(side_effect=lambda cmds, **kw: list(cmds))

        await hc.send_bot_help(mapping)

        embed = hc.context.channel.send.call_args.kwargs["embed"]
        assert "Sem descrição." in embed.fields[0].value


class TestSendCogHelp:
    async def test_one_field_per_command_with_help_text(self, hc):
        cog = _fake_cog("Lures")
        cog.get_commands.return_value = [
            _fake_command("lures", brief="Lista lures", help="Ajuda completa")
        ]
        hc.filter_commands = AsyncMock(side_effect=lambda cmds, **kw: list(cmds))

        await hc.send_cog_help(cog)

        embed = hc.context.channel.send.call_args.kwargs["embed"]
        assert embed.title == "🌸 Lures"
        assert embed.fields[0].name == "!lures"
        assert embed.fields[0].value == "Ajuda completa"

    async def test_falls_back_to_brief_when_no_help_text(self, hc):
        cog = _fake_cog("Lures")
        cog.get_commands.return_value = [
            _fake_command("uselure", brief="Ajusta lures", help=None)
        ]
        hc.filter_commands = AsyncMock(side_effect=lambda cmds, **kw: list(cmds))

        await hc.send_cog_help(cog)

        embed = hc.context.channel.send.call_args.kwargs["embed"]
        assert embed.fields[0].value == "Ajusta lures"


class TestSendGroupHelp:
    async def test_shows_group_description_and_subcommands(self, hc):
        group = MagicMock()
        group.qualified_name = "device"
        group.help = "Gere o dispositivo"
        group.brief = None
        group.commands = [_fake_command("device status", brief="Verifica ligação")]
        hc.filter_commands = AsyncMock(side_effect=lambda cmds, **kw: list(cmds))

        await hc.send_group_help(group)

        embed = hc.context.channel.send.call_args.kwargs["embed"]
        assert embed.title == "📖 !device"
        assert embed.description == "Gere o dispositivo"
        assert embed.fields[0].name == "!device status"

    async def test_falls_back_to_placeholder_when_no_help_or_brief(self, hc):
        group = MagicMock()
        group.qualified_name = "container"
        group.help = None
        group.brief = None
        group.commands = []
        hc.filter_commands = AsyncMock(return_value=[])

        await hc.send_group_help(group)

        embed = hc.context.channel.send.call_args.kwargs["embed"]
        assert embed.description == "Sem descrição."


class TestSendCommandHelp:
    async def test_shows_description_signature_and_aliases(self, hc):
        command = _fake_command(
            "questleiria", help="Pesquisa quests em Leiria", aliases=["questmarinha"]
        )
        hc.get_command_signature = MagicMock(return_value="!questleiria <palavra>")

        await hc.send_command_help(command)

        embed = hc.context.channel.send.call_args.kwargs["embed"]
        assert embed.title == "📖 !questleiria"
        assert embed.description == "Pesquisa quests em Leiria"
        alias_field = next(f for f in embed.fields if f.name == "Aliases")
        assert alias_field.value == "questmarinha"
        usage_field = next(f for f in embed.fields if f.name == "Utilização")
        assert usage_field.value == "`!questleiria <palavra>`"

    async def test_no_aliases_omits_the_field(self, hc):
        command = _fake_command("tracklist", brief="Lista quests seguidas")
        hc.get_command_signature = MagicMock(return_value="!tracklist")

        await hc.send_command_help(command)

        embed = hc.context.channel.send.call_args.kwargs["embed"]
        assert all(f.name != "Aliases" for f in embed.fields)


class TestSendErrorMessage:
    async def test_sends_error_embed(self, hc):
        await hc.send_error_message("Comando 'bogus' não encontrado.")

        embed = hc.context.channel.send.call_args.kwargs["embed"]
        assert "bogus" in embed.title
        assert embed.title.startswith("❌")
