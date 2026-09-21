"""Tests for cogs.event_panel.

The button's work happens in EventPanelView.handle_click, which the
decorated discord.ui button only forwards to -- so the tests call
handle_click directly instead of going through component dispatch.
"""

from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from cogs.event_panel import EventPanelView
from modules.config import Config

_ROLE_ID = 4242
_USER_ID = 123


def _role(position=1):
    role = MagicMock()
    role.id = _ROLE_ID
    role.position = position
    return role


def _member(roles=()):
    member = MagicMock()
    member.id = _USER_ID
    member.roles = list(roles)
    member.add_roles = AsyncMock()
    member.remove_roles = AsyncMock()
    return member


def _guild(role=None, member=None, bot_top_position=10):
    guild = MagicMock()
    guild.get_role.return_value = role
    guild.get_member.return_value = member
    guild.fetch_member = AsyncMock(return_value=member)
    guild.me.top_role.position = bot_top_position
    guild.members = [member] if member is not None else []
    return guild


def _interaction(guild=None, user_id=_USER_ID):
    interaction = MagicMock()
    interaction.guild = guild
    interaction.user = MagicMock()
    interaction.user.id = user_id
    interaction.response.send_message = AsyncMock()
    return interaction


@pytest.fixture
def poliswag():
    bot = MagicMock()
    bot.ADMIN_USERS_IDS = ["111"]
    bot.utility.log_to_file = MagicMock()
    bot.utility.send_embed_to_channel = AsyncMock()
    bot.MOD_CHANNEL = MagicMock()
    bot.EVENT_PANEL_CHANNEL = MagicMock()
    bot.EVENT_PANEL_CHANNEL.send = AsyncMock()
    return bot


@pytest.fixture(autouse=True)
def _role_id(mocker):
    mocker.patch.object(Config, "EVENTS_ROLE_ID", _ROLE_ID)


class TestHandleClick:
    async def test_grants_the_role_when_the_member_does_not_have_it(self, poliswag):
        role = _role()
        member = _member()
        interaction = _interaction(guild=_guild(role=role, member=member))

        await EventPanelView(poliswag).handle_click(interaction)

        member.add_roles.assert_awaited_once()
        assert member.add_roles.await_args.args[0] is role
        member.remove_roles.assert_not_awaited()
        interaction.response.send_message.assert_awaited_once()
        assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True

    async def test_removes_the_role_when_the_member_already_has_it(self, poliswag):
        role = _role()
        member = _member(roles=[role])
        interaction = _interaction(guild=_guild(role=role, member=member))

        await EventPanelView(poliswag).handle_click(interaction)

        member.remove_roles.assert_awaited_once()
        assert member.remove_roles.await_args.args[0] is role
        member.add_roles.assert_not_awaited()

    async def test_the_two_replies_differ(self, poliswag):
        role = _role()
        joined = _interaction(guild=_guild(role=role, member=_member()))
        left = _interaction(guild=_guild(role=role, member=_member(roles=[role])))

        await EventPanelView(poliswag).handle_click(joined)
        await EventPanelView(poliswag).handle_click(left)

        assert (
            joined.response.send_message.await_args.args[0]
            != left.response.send_message.await_args.args[0]
        )

    async def test_the_view_is_persistent(self, poliswag):
        """timeout=None and a fixed custom_id are what let bot.add_view
        revive panels from past events after a restart."""
        view = EventPanelView(poliswag)
        assert view.timeout is None
        assert [child.custom_id for child in view.children] == ["event_panel:toggle"]


class TestHandleClickFailures:
    async def test_missing_role_warns_the_user_and_logs(self, poliswag):
        interaction = _interaction(guild=_guild(role=None, member=_member()))

        await EventPanelView(poliswag).handle_click(interaction)

        interaction.response.send_message.assert_awaited_once()
        assert "admin" in interaction.response.send_message.await_args.args[0]
        assert poliswag.utility.log_to_file.call_args.args[1] == "ERROR"

    async def test_dm_click_resolves_the_member_through_the_panel_channel(
        self, poliswag
    ):
        role = _role()
        member = _member()
        guild = _guild(role=role, member=member)
        poliswag.EVENT_PANEL_CHANNEL.guild = guild
        interaction = _interaction(guild=None)

        await EventPanelView(poliswag).handle_click(interaction)

        guild.get_member.assert_called_once_with(_USER_ID)
        member.add_roles.assert_awaited_once()

    async def test_dm_click_falls_back_to_fetching_the_member(self, poliswag):
        """An uncached member must not look like a non-member."""
        role = _role()
        member = _member()
        guild = _guild(role=role, member=member)
        guild.get_member.return_value = None
        poliswag.EVENT_PANEL_CHANNEL.guild = guild
        interaction = _interaction(guild=None)

        await EventPanelView(poliswag).handle_click(interaction)

        guild.fetch_member.assert_awaited_once_with(_USER_ID)
        member.add_roles.assert_awaited_once()

    async def test_non_member_is_told_so_and_no_role_is_touched(self, poliswag):
        role = _role()
        guild = _guild(role=role, member=None)
        guild.fetch_member = AsyncMock(
            side_effect=discord.NotFound(MagicMock(status=404), "unknown member")
        )
        poliswag.EVENT_PANEL_CHANNEL.guild = guild
        interaction = _interaction(guild=None)

        await EventPanelView(poliswag).handle_click(interaction)

        assert "servidor" in interaction.response.send_message.await_args.args[0]

    async def test_forbidden_apologises_and_tells_the_mods(self, poliswag):
        role = _role()
        member = _member()
        member.add_roles = AsyncMock(
            side_effect=discord.Forbidden(MagicMock(status=403), "missing perms")
        )
        interaction = _interaction(guild=_guild(role=role, member=member))

        await EventPanelView(poliswag).handle_click(interaction)

        interaction.response.send_message.assert_awaited_once()
        assert poliswag.utility.log_to_file.call_args.args[1] == "ERROR"
        poliswag.utility.send_embed_to_channel.assert_awaited_once()
        assert (
            poliswag.utility.send_embed_to_channel.await_args.args[0]
            is poliswag.MOD_CHANNEL
        )

    async def test_forbidden_without_a_mod_channel_still_replies(self, poliswag):
        role = _role()
        member = _member()
        member.add_roles = AsyncMock(
            side_effect=discord.Forbidden(MagicMock(status=403), "missing perms")
        )
        poliswag.MOD_CHANNEL = None
        interaction = _interaction(guild=_guild(role=role, member=member))

        await EventPanelView(poliswag).handle_click(interaction)

        interaction.response.send_message.assert_awaited_once()
