"""Tests for cogs.event_panel.

The button's work happens in EventPanelView.handle_click, which the
decorated discord.ui button only forwards to -- so the tests call
handle_click directly instead of going through component dispatch.
"""

from unittest.mock import AsyncMock, MagicMock

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
