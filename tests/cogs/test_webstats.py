"""Tests for cogs.webstats.WebStats.

Only MY_ID may invoke, and the report only ever goes to MY_ID's DMs — the
statistics must never reach the channel the command was typed in.
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest
from discord.ext import commands

from cogs.webstats import WebStats, setup

MY_ID = 98846248865398784


@pytest.fixture
def cog():
    poliswag = MagicMock()
    poliswag.ADMIN_USERS_IDS = ["42", str(MY_ID)]
    poliswag.page_view_stats = AsyncMock()
    poliswag.page_view_stats.collect.return_value = {"totals": [{"sessions": 1}]}
    poliswag.MOD_CHANNEL = AsyncMock()
    poliswag.get_user.return_value = None
    poliswag.fetch_user = AsyncMock(return_value=AsyncMock())
    return WebStats(poliswag)


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
    async def test_a_bad_period_is_answered_in_the_channel_without_touching_the_db(
        self, cog
    ):
        ctx = make_ctx()
        await WebStats.webstats.callback(cog, ctx, "400d")
        ctx.send.assert_awaited_once()
        cog.poliswag.page_view_stats.collect.assert_not_awaited()
        ctx.message.delete.assert_not_awaited()

    async def test_no_argument_means_the_last_seven_days(self, cog):
        ctx = make_ctx()
        await WebStats.webstats.callback(cog, ctx, None)
        since = cog.poliswag.page_view_stats.collect.await_args.args[0]
        assert (datetime.utcnow() - since).days == 6

    async def test_an_unset_recipient_is_a_config_error(self, cog, _my_id):
        _my_id.MY_ID = 0
        ctx = make_ctx()
        await WebStats.webstats.callback(cog, ctx, None)
        ctx.send.assert_awaited_once()
        cog.poliswag.page_view_stats.collect.assert_not_awaited()


class TestDelivery:
    async def test_the_report_goes_to_my_id_and_never_to_the_channel(self, cog):
        ctx = make_ctx()
        user = cog.poliswag.fetch_user.return_value
        await WebStats.webstats.callback(cog, ctx, None)
        cog.poliswag.fetch_user.assert_awaited_once_with(MY_ID)
        user.send.assert_awaited_once()
        assert "embed" in user.send.await_args.kwargs
        ctx.send.assert_not_awaited()

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
        cog.poliswag.fetch_user.return_value.send.assert_awaited_once()

    async def test_an_oversized_report_is_sent_as_a_file(self, cog):
        ctx = make_ctx()
        user = cog.poliswag.fetch_user.return_value
        with patch("cogs.webstats.build_dm_embed", return_value=None):
            await WebStats.webstats.callback(cog, ctx, None)
        assert "file" in user.send.await_args.kwargs
        assert user.send.await_args.kwargs["file"].filename.startswith("webstats-")


class TestFailures:
    async def test_a_dead_database_is_reported_in_the_channel_not_by_dm(self, cog):
        ctx = make_ctx()
        cog.poliswag.page_view_stats.collect.side_effect = RuntimeError("down")
        await WebStats.webstats.callback(cog, ctx, None)
        ctx.send.assert_awaited_once()
        cog.poliswag.fetch_user.return_value.send.assert_not_awaited()

    async def test_closed_dms_are_reported_to_the_mods_without_the_statistics(
        self, cog
    ):
        ctx = make_ctx()
        user = cog.poliswag.fetch_user.return_value
        user.send.side_effect = discord.Forbidden(MagicMock(), "closed")
        await WebStats.webstats.callback(cog, ctx, None)
        cog.poliswag.MOD_CHANNEL.send.assert_awaited_once()
        sent = str(cog.poliswag.MOD_CHANNEL.send.await_args)
        assert "Sessões" not in sent

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
