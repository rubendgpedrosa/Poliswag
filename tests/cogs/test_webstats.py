"""Tests for cogs.webstats.WebStats.

Only MY_ID may invoke, and everything the command produces goes to MY_ID's
DMs — the statistics and the report link must never reach the channel the
command was typed in.
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest
from discord.ext import commands

from cogs.webstats import WebStats, setup

MY_ID = 98846248865398784
LINK = "https://pogoleiria.pt/webstats/" + "a" * 64


@pytest.fixture
def cog():
    poliswag = MagicMock()
    poliswag.ADMIN_USERS_IDS = ["42", str(MY_ID)]
    poliswag.page_view_stats = AsyncMock()
    poliswag.page_view_stats.collect.return_value = {
        "totals": [{"sessions": 1}],
        "daily": [],
        "views": [],
    }
    poliswag.MOD_CHANNEL = AsyncMock()
    poliswag.get_user.return_value = None
    poliswag.fetch_user = AsyncMock(return_value=AsyncMock())
    cog = WebStats(poliswag)
    cog.access = AsyncMock()
    cog.access.current_or_issue.return_value = LINK
    cog.access.rotate.return_value = LINK
    return cog


def make_ctx(author_id=MY_ID, dm=False):
    ctx = MagicMock()
    ctx.author.id = author_id
    ctx.send = AsyncMock()
    ctx.message.delete = AsyncMock()
    ctx.channel = MagicMock(spec=discord.DMChannel) if dm else MagicMock()
    return ctx


@pytest.fixture(autouse=True)
def _my_id():
    with patch("cogs.webstats.Config") as config:
        config.MY_ID = MY_ID
        yield config


class TestCogCheck:
    def test_my_id_passes(self, cog):
        assert cog.cog_check(make_ctx()) is True

    def test_another_admin_is_refused(self, cog):
        # ADMIN_USERS_IDS gates other commands; this one is MY_ID only.
        assert cog.cog_check(make_ctx(author_id=42)) is False

    def test_a_stranger_is_refused(self, cog):
        assert cog.cog_check(make_ctx(author_id=999)) is False

    async def test_refusal_is_silent(self, cog):
        ctx = make_ctx(author_id=999)
        await cog.cog_command_error(ctx, commands.CheckFailure())
        ctx.send.assert_not_awaited()


class TestArguments:
    async def test_an_unknown_argument_is_answered_without_touching_the_db(self, cog):
        ctx = make_ctx()
        await WebStats.webstats.callback(cog, ctx, "relatorio")
        ctx.send.assert_awaited_once()
        cog.poliswag.page_view_stats.collect.assert_not_awaited()
        ctx.message.delete.assert_not_awaited()

    async def test_a_period_without_export_is_refused(self, cog):
        # `!stats 30d` no longer means anything: the period is chosen in the
        # report, not in Discord.
        ctx = make_ctx()
        await WebStats.webstats.callback(cog, ctx, "30d", "x")
        ctx.send.assert_awaited_once()
        cog.poliswag.page_view_stats.collect.assert_not_awaited()

    async def test_no_argument_means_the_last_24_hours(self, cog):
        ctx = make_ctx()
        await WebStats.webstats.callback(cog, ctx, None)
        since = cog.poliswag.page_view_stats.collect.await_args_list[0].args[0]
        age = (datetime.utcnow() - since).total_seconds()
        assert 23 * 60 * 60 < age < 24 * 60 * 60 + 5

    async def test_an_unset_recipient_is_a_config_error(self, cog, _my_id):
        _my_id.MY_ID = 0
        ctx = make_ctx()
        await WebStats.webstats.callback(cog, ctx, None)
        ctx.send.assert_awaited_once()
        cog.poliswag.page_view_stats.collect.assert_not_awaited()


class TestDelivery:
    async def test_everything_goes_to_my_id_and_never_to_the_channel(self, cog):
        ctx = make_ctx()
        user = cog.poliswag.fetch_user.return_value
        await WebStats.webstats.callback(cog, ctx, None)
        cog.poliswag.fetch_user.assert_awaited_once_with(MY_ID)
        assert "embed" in user.send.await_args_list[0].kwargs
        ctx.send.assert_not_awaited()

    async def test_the_link_is_sent_in_a_message_of_its_own_to_be_copied(self, cog):
        ctx = make_ctx()
        user = cog.poliswag.fetch_user.return_value
        await WebStats.webstats.callback(cog, ctx, None)
        assert LINK in user.send.await_args_list[1].args[0]

    async def test_rotation_issues_a_new_link_and_says_the_old_one_is_dead(self, cog):
        ctx = make_ctx()
        user = cog.poliswag.fetch_user.return_value
        await WebStats.webstats.callback(cog, ctx, "novocodigo")
        cog.access.rotate.assert_awaited_once()
        cog.access.current_or_issue.assert_not_awaited()
        assert "deixou de funcionar" in user.send.await_args_list[1].args[0]

    async def test_an_existing_link_cannot_be_reprinted_so_it_says_so(self, cog):
        # Only the hash is stored. Silently sending no link would read as a
        # failure rather than as the design.
        ctx = make_ctx()
        cog.access.current_or_issue.return_value = None
        user = cog.poliswag.fetch_user.return_value
        await WebStats.webstats.callback(cog, ctx, None)
        assert "novocodigo" in user.send.await_args_list[1].args[0]

    async def test_the_invoking_message_is_removed_from_a_guild_channel(self, cog):
        ctx = make_ctx()
        await WebStats.webstats.callback(cog, ctx, None)
        ctx.message.delete.assert_awaited_once()

    async def test_nothing_is_deleted_in_a_dm(self, cog):
        ctx = make_ctx(dm=True)
        await WebStats.webstats.callback(cog, ctx, None)
        ctx.message.delete.assert_not_awaited()

    async def test_an_undeletable_message_does_not_stop_the_report(self, cog):
        ctx = make_ctx()
        ctx.message.delete.side_effect = discord.Forbidden(MagicMock(), "no")
        await WebStats.webstats.callback(cog, ctx, None)
        cog.poliswag.fetch_user.return_value.send.assert_awaited()


class TestExport:
    async def test_export_sends_aggregates_as_json_only_to_the_owner(self, cog):
        ctx = make_ctx()
        await WebStats.webstats.callback(cog, ctx, "export", "7d")
        kwargs = cog.poliswag.fetch_user.return_value.send.await_args.kwargs
        assert kwargs["file"].filename.endswith(".json")
        assert (
            cog.poliswag.page_view_stats.collect.await_args.kwargs["detail"] == "export"
        )
        ctx.send.assert_not_awaited()

    async def test_export_never_issues_or_rotates_a_link(self, cog):
        ctx = make_ctx()
        await WebStats.webstats.callback(cog, ctx, "export", "7d")
        cog.access.rotate.assert_not_awaited()
        cog.access.current_or_issue.assert_not_awaited()

    async def test_a_bad_export_period_is_answered_without_touching_the_db(self, cog):
        ctx = make_ctx()
        await WebStats.webstats.callback(cog, ctx, "export", "400d")
        ctx.send.assert_awaited_once()
        cog.poliswag.page_view_stats.collect.assert_not_awaited()

    async def test_an_oversized_export_explains_itself_instead_of_failing(self, cog):
        ctx = make_ctx()
        user = cog.poliswag.fetch_user.return_value
        with patch("cogs.webstats.render_export", return_value="x" * 8_000_000):
            await WebStats.webstats.callback(cog, ctx, "export", "all")
        assert "file" not in user.send.await_args.kwargs
        assert "período mais curto" in user.send.await_args.args[0]


class TestFailures:
    async def test_a_dead_database_is_reported_in_the_channel_not_by_dm(self, cog):
        ctx = make_ctx()
        cog.poliswag.page_view_stats.collect.side_effect = RuntimeError("down")
        await WebStats.webstats.callback(cog, ctx, None)
        ctx.send.assert_awaited_once()

    async def test_a_failed_token_issue_is_reported_without_running_a_query(self, cog):
        ctx = make_ctx()
        cog.access.current_or_issue.side_effect = RuntimeError("down")
        await WebStats.webstats.callback(cog, ctx, None)
        ctx.send.assert_awaited_once()
        cog.poliswag.page_view_stats.collect.assert_not_awaited()

    async def test_closed_dms_are_reported_to_the_mods_without_the_statistics(
        self, cog
    ):
        ctx = make_ctx()
        user = cog.poliswag.fetch_user.return_value
        user.send.side_effect = discord.Forbidden(MagicMock(), "closed")
        await WebStats.webstats.callback(cog, ctx, None)
        cog.poliswag.MOD_CHANNEL.send.assert_awaited_once()
        sent = str(cog.poliswag.MOD_CHANNEL.send.await_args)
        assert "visitantes" not in sent

    async def test_a_failed_dm_never_leaks_the_link_to_the_mod_channel(self, cog):
        ctx = make_ctx()
        cog.poliswag.fetch_user.return_value.send.side_effect = discord.HTTPException(
            MagicMock(), "nope"
        )
        await WebStats.webstats.callback(cog, ctx, None)
        assert LINK not in str(cog.poliswag.MOD_CHANNEL.send.await_args)

    async def test_a_missing_mod_channel_only_logs(self, cog):
        ctx = make_ctx()
        cog.poliswag.MOD_CHANNEL = None
        cog.poliswag.fetch_user.return_value.send.side_effect = discord.Forbidden(
            MagicMock(), "closed"
        )
        await WebStats.webstats.callback(cog, ctx, None)  # must not raise


class TestExtension:
    async def test_cog_load_and_unload_announce_themselves(self, cog, capsys):
        await cog.cog_load()
        await cog.cog_unload()
        assert "WebStats" in capsys.readouterr().out

    async def test_setup_registers_the_cog(self):
        bot = AsyncMock()
        await setup(bot)
        bot.add_cog.assert_awaited_once()
