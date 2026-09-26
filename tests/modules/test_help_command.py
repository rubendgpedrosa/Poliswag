"""Tests for modules.help_command.EmbedHelpCommand.

discord.py's HelpCommand base class does real permission filtering
(filter_commands) and command-signature formatting (get_command_signature)
against live Command objects, neither of which are worth reconstructing with
mocks -- both are stubbed per test so we can focus on the embed shape our
overrides build.
"""

from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from discord.ext import commands

from modules.config import Config
from modules.help_command import EmbedHelpCommand
from modules.permissions import is_mod, is_owner


def _fake_command(name, brief=None, help=None, aliases=None, checks=None):
    cmd = MagicMock()
    cmd.qualified_name = name
    cmd.name = name
    cmd.brief = brief
    cmd.help = help
    cmd.aliases = aliases or []
    cmd.checks = checks or []
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
    help_command.context.bot.ADMIN_USERS_IDS = ["111", "999"]
    help_command.context.author.id = 555  # a member
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


class _OpenCog(commands.Cog, name="Pokedex"):
    pass


class _ModCog(commands.Cog, name="Tracker"):
    def cog_check(self, ctx):
        return True


class _QuestsCog(commands.Cog, name="Quests"):
    pass


class _OwnerCog(commands.Cog, name="WebStats"):
    def cog_check(self, ctx):
        return True


def _send(hc):
    return hc.context.channel.send.call_args.kwargs["embed"]


def _field(embed, name):
    return next((f for f in embed.fields if f.name == name), None)


class TestSendBotHelp:
    @pytest.fixture
    def mapping(self):
        return {
            _OpenCog(): [_fake_command("pokedex", brief="Código da Pokédex")],
            _QuestsCog(): [
                _fake_command("questleiria", brief="Procura quests"),
                _fake_command("scan", brief="Scan", checks=[is_mod]),
            ],
            _ModCog(): [_fake_command("track"), _fake_command("untrack")],
            _OwnerCog(): [_fake_command("stats")],
        }

    @staticmethod
    def _as(hc, author_id, visible):
        """`visible`: the cog names filter_commands lets through (it runs
        each cog_check in discord.py; stubbed here)."""
        hc.context.author.id = author_id

        async def _filter(cmds, **kw):
            cmds = list(cmds)
            return [c for c in cmds if c.cog_name in visible]

        hc.filter_commands = _filter

    @staticmethod
    def _tag(mapping):
        for cog, cmds in mapping.items():
            for c in cmds:
                c.cog_name = cog.qualified_name

    async def test_member_sees_only_public_commands_with_descriptions(
        self, hc, mapping
    ):
        self._tag(mapping)
        self._as(hc, 555, {"Pokedex", "Quests"})

        await hc.send_bot_help(mapping)

        embed = _send(hc)
        assert [f.name for f in embed.fields] == ["Para todos"]
        value = embed.fields[0].value
        assert "`!pokedex` — Código da Pokédex" in value
        assert "`!questleiria` `!questmarinha` — Procura quests" in value
        # Gated inside its body, so filter_commands can't hide it: !help must.
        assert "!scan" not in value

    async def test_mod_gets_compact_topic_lines_and_no_owner_section(self, hc, mapping):
        self._tag(mapping)
        self._as(hc, 111, {"Pokedex", "Quests", "Tracker"})

        await hc.send_bot_help(mapping)

        embed = _send(hc)
        assert [f.name for f in embed.fields] == ["Para todos", "🛡️ Mods"]
        assert (
            _field(embed, "🛡️ Mods").value == "**Quests:** `!scan` `!track` `!untrack`"
        )

    async def test_owner_gets_all_three_sections(self, hc, mapping, mocker):
        mocker.patch.object(Config, "MY_ID", 999)
        self._tag(mapping)
        self._as(hc, 999, {"Pokedex", "Quests", "Tracker", "WebStats"})

        await hc.send_bot_help(mapping)

        embed = _send(hc)
        assert [f.name for f in embed.fields] == [
            "Para todos",
            "🛡️ Mods",
            "🔒 Só para ti",
        ]
        assert _field(embed, "🔒 Só para ti").value == "`!stats`"

    async def test_owner_check_on_a_single_command_puts_it_in_owner_section(
        self, hc, mocker
    ):
        mocker.patch.object(Config, "MY_ID", 999)
        cmd = _fake_command("secret", brief="x", checks=[is_owner])
        cmd.cog_name = "Pokedex"
        self._as(hc, 999, {"Pokedex"})

        await hc.send_bot_help({_OpenCog(): [cmd]})

        assert [f.name for f in _send(hc).fields] == ["🔒 Só para ti"]

    async def test_questleiria_is_listed_with_its_marinha_alias(self, hc):
        cmd = _fake_command("questleiria", brief="Procura quests")
        cmd.cog_name = "Quests"
        self._as(hc, 555, {"Quests"})

        await hc.send_bot_help({_QuestsCog(): [cmd]})

        assert (
            "`!questleiria` `!questmarinha` — Procura quests"
            in _send(hc).fields[0].value
        )

    async def test_missing_brief_shows_placeholder(self, hc):
        cmd = _fake_command("pokedex", brief=None)
        cmd.cog_name = "Pokedex"
        self._as(hc, 555, {"Pokedex"})

        await hc.send_bot_help({_OpenCog(): [cmd]})

        assert "Sem descrição." in _send(hc).fields[0].value

    async def test_nothing_visible_sends_no_fields(self, hc):
        self._as(hc, 555, set())

        await hc.send_bot_help({_ModCog(): [_fake_command("track")]})

        assert _send(hc).fields == []


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


class TestCommandNotFound:
    def test_returns_pt_message(self, hc):
        msg = hc.command_not_found("bogus")
        assert msg == 'Não encontrei nenhum comando chamado "bogus".'


class TestSubcommandNotFound:
    def test_group_with_subcommands_names_the_missing_one(self, hc):
        group = MagicMock(spec=commands.Group)
        group.qualified_name = "device"
        group.all_commands = {"status": MagicMock()}
        msg = hc.subcommand_not_found(group, "bogus")
        assert "device" in msg
        assert "bogus" in msg

    def test_group_without_subcommands(self, hc):
        group = MagicMock(spec=commands.Group)
        group.qualified_name = "empty"
        group.all_commands = {}
        msg = hc.subcommand_not_found(group, "bogus")
        assert "não tem subcomandos" in msg

    def test_non_group_command(self, hc):
        command = _fake_command("track")
        msg = hc.subcommand_not_found(command, "bogus")
        assert "não tem subcomandos" in msg


class TestDeletesTheCommand:
    async def test_command_message_is_deleted_in_a_channel(self, hc):
        ctx = MagicMock()
        ctx.message.delete = AsyncMock()

        await hc.prepare_help_command(ctx)

        ctx.message.delete.assert_awaited_once()

    async def test_nothing_to_delete_in_a_dm(self, hc):
        ctx = MagicMock(guild=None)
        ctx.message.delete = AsyncMock()

        await hc.prepare_help_command(ctx)

        ctx.message.delete.assert_not_awaited()

    async def test_a_failed_delete_is_ignored(self, hc):
        ctx = MagicMock()
        ctx.message.delete = AsyncMock(
            side_effect=discord.NotFound(MagicMock(status=404), "Unknown Message")
        )

        await hc.prepare_help_command(ctx)  # does not raise
