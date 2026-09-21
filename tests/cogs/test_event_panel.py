"""Tests for cogs.event_panel.

The button's work happens in EventPanelView.handle_click, which the
decorated discord.ui button only forwards to -- so the tests call
handle_click directly instead of going through component dispatch.
"""

from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from cogs.event_panel import _CLEAR_REASON, EventPanel, EventPanelView, setup
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
    member.mention = f"<@{_USER_ID}>"
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
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()
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
        interaction.followup.send.assert_awaited_once()
        assert interaction.followup.send.await_args.kwargs["ephemeral"] is True

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
            joined.followup.send.await_args.args[0]
            != left.followup.send.await_args.args[0]
        )

    async def test_the_view_is_persistent(self, poliswag):
        """timeout=None and a fixed custom_id are what let bot.add_view
        revive panels from past events after a restart."""
        view = EventPanelView(poliswag)
        assert view.timeout is None
        assert [child.custom_id for child in view.children] == ["event_panel:toggle"]

    async def test_the_click_is_acknowledged_before_the_role_call(self, poliswag):
        """Discord drops an interaction that is not acked within 3s, so the
        ack has to come before the HTTP work rather than after it."""
        order = []
        role = _role()
        member = _member()
        member.add_roles = AsyncMock(
            side_effect=lambda *a, **k: order.append("add_roles")
        )
        interaction = _interaction(guild=_guild(role=role, member=member))
        interaction.response.defer = AsyncMock(
            side_effect=lambda **k: order.append("defer")
        )

        await EventPanelView(poliswag).handle_click(interaction)

        assert order == ["defer", "add_roles"]

    async def test_the_button_itself_reaches_handle_click(self, poliswag):
        """The decorated button callback is what Discord actually invokes;
        every other test calls handle_click directly, so without this the
        one wire between a click and the code is untested."""
        view = EventPanelView(poliswag)
        view.handle_click = AsyncMock()
        interaction = _interaction(guild=_guild(role=_role(), member=_member()))

        await view.children[0].callback(interaction)

        view.handle_click.assert_awaited_once_with(interaction)


class TestHandleClickFailures:
    async def test_missing_role_warns_the_user_and_logs(self, poliswag):
        interaction = _interaction(guild=_guild(role=None, member=_member()))

        await EventPanelView(poliswag).handle_click(interaction)

        interaction.followup.send.assert_awaited_once()
        assert "admin" in interaction.followup.send.await_args.args[0]
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

        assert "servidor" in interaction.followup.send.await_args.args[0]

    async def test_forbidden_apologises_and_tells_the_mods(self, poliswag):
        role = _role()
        member = _member()
        member.add_roles = AsyncMock(
            side_effect=discord.Forbidden(MagicMock(status=403), "missing perms")
        )
        interaction = _interaction(guild=_guild(role=role, member=member))

        await EventPanelView(poliswag).handle_click(interaction)

        interaction.followup.send.assert_awaited_once()
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

        interaction.followup.send.assert_awaited_once()
        poliswag.utility.send_embed_to_channel.assert_not_awaited()

    async def test_forbidden_while_removing_mentions_removal(self, poliswag):
        role = _role()
        member = _member(roles=[role])
        member.remove_roles = AsyncMock(
            side_effect=discord.Forbidden(MagicMock(status=403), "missing perms")
        )
        interaction = _interaction(guild=_guild(role=role, member=member))

        await EventPanelView(poliswag).handle_click(interaction)

        interaction.followup.send.assert_awaited_once()
        assert "remover" in poliswag.utility.log_to_file.call_args.args[0]


def _ctx(author_id="111"):
    ctx = MagicMock()
    ctx.author = MagicMock()
    ctx.author.id = author_id
    ctx.author.send = AsyncMock()
    ctx.send = AsyncMock()
    return ctx


class TestPostCommand:
    async def test_posts_the_panel_with_a_working_view(self, poliswag):
        guild = _guild(role=_role(position=1), member=_member(), bot_top_position=10)
        poliswag.EVENT_PANEL_CHANNEL.guild = guild
        cog = EventPanel(poliswag)

        await cog.eventpanel(cog, _ctx())

        poliswag.EVENT_PANEL_CHANNEL.send.assert_awaited_once()
        kwargs = poliswag.EVENT_PANEL_CHANNEL.send.await_args.kwargs
        assert isinstance(kwargs["view"], EventPanelView)
        assert isinstance(kwargs["embed"], discord.Embed)

    async def test_refuses_when_the_role_outranks_the_bot(self, poliswag):
        guild = _guild(role=_role(position=20), member=_member(), bot_top_position=10)
        poliswag.EVENT_PANEL_CHANNEL.guild = guild
        cog = EventPanel(poliswag)
        ctx = _ctx()

        await cog.eventpanel(cog, ctx)

        poliswag.EVENT_PANEL_CHANNEL.send.assert_not_awaited()
        ctx.send.assert_awaited_once()
        description = ctx.send.await_args.kwargs["embed"].description
        assert "Arrasta-o" in description

    async def test_refuses_when_the_role_ties_with_the_bot(self, poliswag):
        """Equal position is still unmanageable: Discord only lets a bot
        touch roles strictly below its own."""
        guild = _guild(role=_role(position=10), member=_member(), bot_top_position=10)
        poliswag.EVENT_PANEL_CHANNEL.guild = guild
        cog = EventPanel(poliswag)
        ctx = _ctx()

        await cog.eventpanel(cog, ctx)

        poliswag.EVENT_PANEL_CHANNEL.send.assert_not_awaited()
        ctx.send.assert_awaited_once()
        description = ctx.send.await_args.kwargs["embed"].description
        assert "Arrasta-o" in description

    async def test_refuses_when_the_role_is_missing(self, poliswag):
        poliswag.EVENT_PANEL_CHANNEL.guild = _guild(role=None)
        cog = EventPanel(poliswag)
        ctx = _ctx()

        await cog.eventpanel(cog, ctx)

        poliswag.EVENT_PANEL_CHANNEL.send.assert_not_awaited()
        ctx.send.assert_awaited_once()
        description = ctx.send.await_args.kwargs["embed"].description
        assert "EVENTS_ROLE_ID" in description

    async def test_refuses_when_the_panel_channel_is_unset(self, poliswag):
        poliswag.EVENT_PANEL_CHANNEL = None
        cog = EventPanel(poliswag)
        ctx = _ctx()

        await cog.eventpanel(cog, ctx)

        ctx.send.assert_awaited_once()
        description = ctx.send.await_args.kwargs["embed"].description
        assert "EVENT_PANEL_CHANNEL_ID" in description

    async def test_the_confirmation_names_the_role_and_the_channel(self, poliswag):
        role = _role(position=1)
        role.name = "Eventos"
        guild = _guild(role=role, member=_member(), bot_top_position=10)
        poliswag.EVENT_PANEL_CHANNEL.guild = guild
        poliswag.EVENT_PANEL_CHANNEL.mention = "#anuncios"
        cog = EventPanel(poliswag)
        ctx = _ctx()

        await cog.eventpanel(cog, ctx)

        description = ctx.send.await_args.kwargs["embed"].description
        assert "Eventos" in description
        assert "#anuncios" in description

    async def test_reports_when_the_channel_refuses_the_post(self, poliswag):
        guild = _guild(role=_role(position=1), member=_member(), bot_top_position=10)
        poliswag.EVENT_PANEL_CHANNEL.guild = guild
        poliswag.EVENT_PANEL_CHANNEL.send = AsyncMock(
            side_effect=discord.Forbidden(MagicMock(status=403), "no perms")
        )
        cog = EventPanel(poliswag)
        ctx = _ctx()

        await cog.eventpanel(cog, ctx)

        ctx.send.assert_awaited_once()
        assert "❌" in ctx.send.await_args.kwargs["embed"].title
        assert poliswag.utility.log_to_file.call_args.args[1] == "ERROR"

    def test_only_admins_may_run_it(self, poliswag):
        cog = EventPanel(poliswag)
        assert cog.cog_check(_ctx(author_id="111")) is True
        assert cog.cog_check(_ctx(author_id="222")) is False


class TestTestCommand:
    async def test_dms_the_panel_to_the_caller(self, poliswag):
        guild = _guild(role=_role(position=1), member=_member(), bot_top_position=10)
        poliswag.EVENT_PANEL_CHANNEL.guild = guild
        cog = EventPanel(poliswag)
        ctx = _ctx()

        await cog.eventpanel_test(cog, ctx)

        ctx.author.send.assert_awaited_once()
        kwargs = ctx.author.send.await_args.kwargs
        assert isinstance(kwargs["view"], EventPanelView)
        poliswag.EVENT_PANEL_CHANNEL.send.assert_not_awaited()
        ctx.send.assert_awaited_once()
        assert "DM" in ctx.send.await_args.kwargs["embed"].title

    async def test_reports_when_dms_are_closed(self, poliswag):
        guild = _guild(role=_role(position=1), member=_member(), bot_top_position=10)
        poliswag.EVENT_PANEL_CHANNEL.guild = guild
        cog = EventPanel(poliswag)
        ctx = _ctx()
        ctx.author.send = AsyncMock(
            side_effect=discord.Forbidden(MagicMock(status=403), "DMs closed")
        )

        await cog.eventpanel_test(cog, ctx)

        ctx.send.assert_awaited_once()
        assert "DM" in ctx.send.await_args.kwargs["embed"].title

    async def test_still_runs_the_preflight(self, poliswag):
        poliswag.EVENT_PANEL_CHANNEL.guild = _guild(role=None)
        cog = EventPanel(poliswag)
        ctx = _ctx()

        await cog.eventpanel_test(cog, ctx)

        ctx.author.send.assert_not_awaited()
        ctx.send.assert_awaited_once()
        assert "EVENTS_ROLE_ID" in ctx.send.await_args.kwargs["embed"].description

    async def test_the_dm_button_grants_the_role_for_real(self, poliswag):
        """A DM interaction has no guild, so the view resolves the clicker
        back through the panel channel -- the rehearsal is the real thing."""
        role = _role(position=1)
        member = _member()
        guild = _guild(role=role, member=member, bot_top_position=10)
        poliswag.EVENT_PANEL_CHANNEL.guild = guild
        cog = EventPanel(poliswag)

        ctx = _ctx()
        await cog.eventpanel_test(cog, ctx)
        view = ctx.author.send.await_args.kwargs["view"]

        dm_interaction = _interaction(guild=None)
        await view.handle_click(dm_interaction)

        member.add_roles.assert_awaited_once()
        assert member.add_roles.await_args.args[0] is role


class TestClearCommand:
    async def test_without_confirm_it_only_counts(self, poliswag):
        role = _role(position=1)
        holder = _member(roles=[role])
        guild = _guild(role=role, member=holder, bot_top_position=10)
        guild.members = [holder, _member()]
        poliswag.EVENT_PANEL_CHANNEL.guild = guild
        cog = EventPanel(poliswag)
        ctx = _ctx()

        await cog.eventpanel_clear(cog, ctx, None)

        holder.remove_roles.assert_not_awaited()
        ctx.send.assert_awaited_once()
        description = ctx.send.await_args.kwargs["embed"].description
        assert "1" in description
        assert "confirm" in description

    async def test_confirm_removes_the_role_from_every_holder(self, poliswag):
        role = _role(position=1)
        first = _member(roles=[role])
        second = _member(roles=[role])
        guild = _guild(role=role, member=first, bot_top_position=10)
        guild.members = [first, second, _member()]
        poliswag.EVENT_PANEL_CHANNEL.guild = guild
        cog = EventPanel(poliswag)
        ctx = _ctx()

        await cog.eventpanel_clear(cog, ctx, "confirm")

        first.remove_roles.assert_awaited_once()
        second.remove_roles.assert_awaited_once()
        assert first.remove_roles.await_args.args[0] is role
        assert first.remove_roles.await_args.kwargs["reason"] == _CLEAR_REASON
        assert second.remove_roles.await_args.kwargs["reason"] == _CLEAR_REASON
        description = ctx.send.await_args.kwargs["embed"].description
        assert "2" in description

    async def test_one_failing_member_does_not_abort_the_sweep(self, poliswag):
        role = _role(position=1)
        broken = _member(roles=[role])
        broken.remove_roles = AsyncMock(
            side_effect=discord.Forbidden(MagicMock(status=403), "nope")
        )
        ok = _member(roles=[role])
        guild = _guild(role=role, member=broken, bot_top_position=10)
        guild.members = [broken, ok]
        poliswag.EVENT_PANEL_CHANNEL.guild = guild
        cog = EventPanel(poliswag)
        ctx = _ctx()

        await cog.eventpanel_clear(cog, ctx, "confirm")

        ok.remove_roles.assert_awaited_once()
        assert poliswag.utility.log_to_file.call_args.args[1] == "ERROR"
        ctx.send.assert_awaited_once()
        description = ctx.send.await_args.kwargs["embed"].description
        assert "1" in description
        assert "Falhou" in description
        embed = ctx.send.await_args.kwargs["embed"]
        assert "<@123>" in embed.description
        assert embed.color.value == 0xE74C3C

    async def test_every_removal_failing_is_still_reported(self, poliswag):
        role = _role(position=1)
        holders = [_member(roles=[role]), _member(roles=[role])]
        for holder in holders:
            holder.remove_roles = AsyncMock(
                side_effect=discord.Forbidden(MagicMock(status=403), "nope")
            )
        guild = _guild(role=role, member=holders[0], bot_top_position=10)
        guild.members = holders
        poliswag.EVENT_PANEL_CHANNEL.guild = guild
        cog = EventPanel(poliswag)
        ctx = _ctx()

        await cog.eventpanel_clear(cog, ctx, "confirm")

        description = ctx.send.await_args.kwargs["embed"].description
        assert "**0**" in description
        assert "Falhou em **2**" in description

    async def test_nobody_to_clear_says_so(self, poliswag):
        role = _role(position=1)
        guild = _guild(role=role, member=_member(), bot_top_position=10)
        guild.members = [_member()]
        poliswag.EVENT_PANEL_CHANNEL.guild = guild
        cog = EventPanel(poliswag)
        ctx = _ctx()

        await cog.eventpanel_clear(cog, ctx, "confirm")

        ctx.send.assert_awaited_once()
        description = ctx.send.await_args.kwargs["embed"].description
        assert "Ninguém" in description

    async def test_nobody_to_clear_beats_the_confirmation_gate(self, poliswag):
        """Bare `!eventpanel clear` on an empty role should say there is
        nothing to do, not ask anyone to confirm removing it from zero
        people -- which is what happens if these two branches swap."""
        role = _role(position=1)
        guild = _guild(role=role, member=_member(), bot_top_position=10)
        guild.members = [_member()]
        poliswag.EVENT_PANEL_CHANNEL.guild = guild
        cog = EventPanel(poliswag)
        ctx = _ctx()

        await cog.eventpanel_clear(cog, ctx, None)

        description = ctx.send.await_args.kwargs["embed"].description
        assert "Ninguém" in description
        assert "confirm" not in description

    async def test_still_runs_the_preflight(self, poliswag):
        poliswag.EVENT_PANEL_CHANNEL.guild = _guild(role=None)
        cog = EventPanel(poliswag)
        ctx = _ctx()

        await cog.eventpanel_clear(cog, ctx, "confirm")

        ctx.send.assert_awaited_once()
        description = ctx.send.await_args.kwargs["embed"].description
        assert "EVENTS_ROLE_ID" in description

    async def test_the_count_ignores_members_without_the_role(self, poliswag):
        role = _role(position=1)
        holders = [_member(roles=[role]), _member(roles=[role])]
        guild = _guild(role=role, member=holders[0], bot_top_position=10)
        guild.members = holders + [_member(), _member(), _member()]
        poliswag.EVENT_PANEL_CHANNEL.guild = guild
        cog = EventPanel(poliswag)
        ctx = _ctx()

        await cog.eventpanel_clear(cog, ctx, None)

        assert "2" in ctx.send.await_args.kwargs["embed"].description

    async def test_a_long_failure_list_is_truncated(self, poliswag):
        role = _role(position=1)
        holders = []
        for _ in range(12):
            holder = _member(roles=[role])
            holder.remove_roles = AsyncMock(
                side_effect=discord.Forbidden(MagicMock(status=403), "nope")
            )
            holders.append(holder)
        guild = _guild(role=role, member=holders[0], bot_top_position=10)
        guild.members = holders
        poliswag.EVENT_PANEL_CHANNEL.guild = guild
        cog = EventPanel(poliswag)
        ctx = _ctx()

        await cog.eventpanel_clear(cog, ctx, "confirm")

        description = ctx.send.await_args.kwargs["embed"].description
        assert "Falhou em **12**" in description
        assert "(+2)" in description


class TestSetup:
    async def test_setup_adds_the_cog(self, poliswag):
        poliswag.add_cog = AsyncMock()
        await setup(poliswag)
        poliswag.add_cog.assert_awaited_once()


class TestWiring:
    """main.py is what turns this from a module into a feature. These
    check placement, not just presence: add_view in __init__ would raise
    RuntimeError at startup, and the channel attribute has to exist
    before on_ready resolves it."""

    def test_the_cog_is_loaded_and_the_view_registered_in_setup_hook(self):
        import inspect

        import main

        source = inspect.getsource(main.Poliswag.setup_hook)
        assert 'await self.load_extension("cogs.event_panel")' in source
        assert "self.add_view(EventPanelView(self))" in source

    def test_the_channel_attribute_exists_before_on_ready(self):
        import inspect

        import main

        source = inspect.getsource(main.Poliswag.__init__)
        assert "self.EVENT_PANEL_CHANNEL = None" in source
        assert "add_view" not in source

    def test_the_panel_channel_is_resolved_on_ready(self):
        import inspect

        import main

        source = inspect.getsource(main.Poliswag.get_channels)
        assert '"EVENT_PANEL_CHANNEL": Config.EVENT_PANEL_CHANNEL_ID' in source
