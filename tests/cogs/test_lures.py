"""Tests for cogs.lures.Lures.

The cog uses poliswag.lure_manager (a MagicMock here). build_embed is patched
at the import site inside cogs.lures so we assert on the args, not discord.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cogs.lures import Lures


@pytest.fixture
def cog():
    poliswag = MagicMock()
    poliswag.ADMIN_USERS_IDS = ["42"]
    poliswag.lure_manager = MagicMock()
    return Lures(poliswag)


def make_ctx(author_id="42"):
    ctx = MagicMock()
    ctx.author.id = author_id
    ctx.author.name = "tester"
    ctx.send = AsyncMock()
    return ctx


class TestCogCheck:
    def test_admin_passes(self, cog):
        assert cog.cog_check(make_ctx()) is True

    def test_non_admin_fails(self, cog):
        assert cog.cog_check(make_ctx(author_id="nope")) is False


class TestLures:
    async def test_empty_sends_no_accounts_message(self, cog):
        ctx = make_ctx()
        cog.lure_manager.list_available_with_lures.return_value = []
        with patch("cogs.lures.build_embed", return_value="EMBED") as be:
            await Lures.lures.callback(cog, ctx)
        ctx.send.assert_awaited_once_with(embed="EMBED")
        title, desc = be.call_args.args[0], be.call_args.args[1]
        assert "DISPONÍVEIS" in title
        assert "Não há contas" in desc

    async def test_lists_accounts_with_credentials_and_counts(self, cog):
        ctx = make_ctx()
        cog.lure_manager.list_available_with_lures.return_value = [
            {"username": "free_low", "password": "pw_low", "nb_lures": 2},
            {"username": "free_mid", "password": "pw_mid", "nb_lures": 7},
        ]
        with patch("cogs.lures.build_embed", return_value="EMBED") as be:
            await Lures.lures.callback(cog, ctx)
        desc = be.call_args.args[1]
        assert "free_low / pw_low — 2 lures" in desc
        assert "free_mid / pw_mid — 7 lures" in desc
        ctx.send.assert_awaited_once_with(embed="EMBED")


class TestUseLure:
    async def test_missing_args_sends_usage(self, cog):
        ctx = make_ctx()
        await Lures.uselure.callback(cog, ctx, username=None, number=None)
        cog.lure_manager.adjust_lure_count.assert_not_called()
        assert "Utilização" in ctx.send.call_args.args[0]

    async def test_non_integer_number_sends_usage(self, cog):
        ctx = make_ctx()
        await Lures.uselure.callback(cog, ctx, username="free_low", number="abc")
        cog.lure_manager.adjust_lure_count.assert_not_called()
        assert "inteiro" in ctx.send.call_args.args[0]

    async def test_unknown_username_reports_not_found(self, cog):
        ctx = make_ctx()
        cog.lure_manager.adjust_lure_count.return_value = 0
        await Lures.uselure.callback(cog, ctx, username="ghost", number="-2")
        assert "não foi encontrada" in ctx.send.call_args.args[0]

    async def test_remove_lures_success_logs_and_confirms(self, cog):
        ctx = make_ctx()
        cog.lure_manager.adjust_lure_count.return_value = 1
        with patch("cogs.lures.build_embed", return_value="EMBED") as be:
            await Lures.uselure.callback(cog, ctx, username="free_low", number="-3")
        cog.lure_manager.adjust_lure_count.assert_called_once_with("free_low", -3)
        cog.poliswag.utility.log_to_file.assert_called_once()
        desc = be.call_args.args[1]
        assert "3 lures removidas" in desc
        assert "free_low" in desc

    async def test_add_single_lure_uses_singular(self, cog):
        ctx = make_ctx()
        cog.lure_manager.adjust_lure_count.return_value = 1
        with patch("cogs.lures.build_embed", return_value="EMBED") as be:
            await Lures.uselure.callback(cog, ctx, username="free_low", number="1")
        cog.lure_manager.adjust_lure_count.assert_called_once_with("free_low", 1)
        desc = be.call_args.args[1]
        assert "1 lure adicionada" in desc
