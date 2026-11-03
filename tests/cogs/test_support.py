"""Tests for cogs.support.Support.

`!mbway` hands the owner's payment details to one person. Two things must
hold whatever else changes: the details only ever leave the bot by DM, and an
unconfigured MBWAY_DETAILS points at the public Revolut link instead of
answering nothing.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from cogs.support import Support, setup

REVOLUT = "https://revolut.me/rubendgpedrosa"
DETAILS = "MB Way: 912 345 678 (faynn)"


@pytest.fixture
def cog():
    return Support(MagicMock())


def make_ctx(dm=False):
    ctx = MagicMock()
    ctx.author.mention = "<@42>"
    ctx.author.send = AsyncMock()
    ctx.send = AsyncMock()
    ctx.message.delete = AsyncMock()
    ctx.guild = None if dm else MagicMock()
    return ctx


def embed_text(send_call):
    return send_call.kwargs["embed"].description


class TestTheDetails:
    async def test_goes_to_the_author_by_dm(self, cog):
        ctx = make_ctx(dm=True)
        with patch("cogs.support.Config") as config:
            config.MBWAY_DETAILS = DETAILS

            await cog.mbway.callback(cog, ctx)

        ctx.author.send.assert_awaited_once()
        assert embed_text(ctx.author.send.await_args) == DETAILS
        ctx.send.assert_not_awaited()

    async def test_surrounding_whitespace_is_not_forwarded(self, cog):
        ctx = make_ctx(dm=True)
        with patch("cogs.support.Config") as config:
            config.MBWAY_DETAILS = f"  {DETAILS}\n"

            await cog.mbway.callback(cog, ctx)

        assert embed_text(ctx.author.send.await_args) == DETAILS

    async def test_unset_points_at_revolut(self, cog):
        ctx = make_ctx(dm=True)
        with patch("cogs.support.Config") as config:
            config.MBWAY_DETAILS = ""

        await cog.mbway.callback(cog, ctx)

        assert REVOLUT in embed_text(ctx.author.send.await_args)


class TestInAChannel:
    async def test_the_command_message_goes_first(self, cog):
        ctx = make_ctx()
        with patch("cogs.support.Config") as config:
            config.MBWAY_DETAILS = DETAILS

        await cog.mbway.callback(cog, ctx)

        ctx.message.delete.assert_awaited_once()
        ctx.author.send.assert_awaited_once()
        # The details never land in the channel.
        ctx.send.assert_not_awaited()

    async def test_a_missing_manage_messages_is_not_fatal(self, cog):
        ctx = make_ctx()
        ctx.message.delete.side_effect = discord.Forbidden(MagicMock(), "no")
        with patch("cogs.support.Config") as config:
            config.MBWAY_DETAILS = DETAILS

        await cog.mbway.callback(cog, ctx)

        ctx.author.send.assert_awaited_once()

    async def test_closed_dms_still_answer_without_the_details(self, cog):
        ctx = make_ctx()
        ctx.author.send.side_effect = discord.Forbidden(MagicMock(), "closed")
        with patch("cogs.support.Config") as config:
            config.MBWAY_DETAILS = DETAILS

        await cog.mbway.callback(cog, ctx)

        ctx.send.assert_awaited_once()
        channel_text = embed_text(ctx.send.await_args)
        assert "<@42>" in channel_text
        assert DETAILS not in channel_text


class TestWiring:
    async def test_setup_adds_the_cog(self):
        poliswag = MagicMock()
        poliswag.add_cog = AsyncMock()

        await setup(poliswag)

        poliswag.add_cog.assert_awaited_once()

    def test_the_cog_may_be_used_in_a_dm(self):
        """modules/dm_policy refuses strangers' DMs cog by cog; without this
        the command answers nobody but the owner and the admins."""
        from modules.dm_policy import DM_EXEMPT_COGS

        assert Support.__cog_name__ in DM_EXEMPT_COGS


class TestTheOwnerNote:
    """The owner is told who asked, and whether it is a first ask or a habit."""

    def make_cog(self, tally=1, db_fails=False, owner=None):
        from unittest.mock import AsyncMock

        poliswag = MagicMock()
        poliswag.db.execute_query_to_database = AsyncMock()
        if db_fails:
            poliswag.db.get_data_from_database = AsyncMock(
                side_effect=RuntimeError("db down")
            )
        else:
            poliswag.db.get_data_from_database = AsyncMock(
                return_value=[{"requests": tally, "first_requested_at": None}]
            )
        owner = owner if owner is not None else MagicMock()
        owner.send = AsyncMock()
        poliswag.get_user = MagicMock(return_value=owner)
        poliswag.fetch_user = AsyncMock(return_value=owner)
        return Support(poliswag), owner

    async def test_first_request_names_the_member_and_says_it_is_the_first(self):
        cog, owner = self.make_cog(tally=1)
        ctx = make_ctx(dm=True)
        with patch("cogs.support.Config") as config:
            config.MBWAY_DETAILS = DETAILS
            config.MY_ID = 7

            await cog.mbway.callback(cog, ctx)

        owner.send.assert_awaited_once()
        note = owner.send.await_args.kwargs["embed"].description
        assert "<@42>" not in note
        assert "Primeiro pedido." in note
        ctx.author.send.assert_awaited_once()

    async def test_repeat_request_carries_the_count(self):
        cog, owner = self.make_cog(tally=3)
        ctx = make_ctx(dm=True)
        with patch("cogs.support.Config") as config:
            config.MBWAY_DETAILS = DETAILS
            config.MY_ID = 7

            await cog.mbway.callback(cog, ctx)

        note = owner.send.await_args.kwargs["embed"].description
        assert "3.º pedido" in note

    async def test_a_broken_ledger_does_not_cost_the_member_the_details(self):
        cog, owner = self.make_cog(db_fails=True)
        ctx = make_ctx(dm=True)
        with patch("cogs.support.Config") as config:
            config.MBWAY_DETAILS = DETAILS
            config.MY_ID = 7

            await cog.mbway.callback(cog, ctx)

        ctx.author.send.assert_awaited_once()
        assert embed_text(ctx.author.send.await_args) == DETAILS
        owner.send.assert_not_awaited()

    async def test_no_owner_configured_means_no_note(self):
        cog, owner = self.make_cog()
        ctx = make_ctx(dm=True)
        with patch("cogs.support.Config") as config:
            config.MBWAY_DETAILS = DETAILS
            config.MY_ID = 0

            await cog.mbway.callback(cog, ctx)

        owner.send.assert_not_awaited()
        ctx.author.send.assert_awaited_once()
