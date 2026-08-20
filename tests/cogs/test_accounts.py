"""Tests for cogs.accounts.Accounts.

The cog is a thin delegator over poliswag.account_monitor's status embed
builder. We invoke command callbacks directly via
``Accounts.account_report_cmd.callback(cog, ctx)`` to bypass discord's
dispatcher and test the error-handling branch in isolation.
"""

from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from cogs.accounts import Accounts, setup


@pytest.fixture
def cog():
    poliswag = MagicMock()
    poliswag.account_monitor.get_account_stats = AsyncMock(return_value={"good": 1})
    poliswag.account_monitor.is_device_connected = AsyncMock(return_value=True)
    poliswag.account_monitor.build_status_embed = MagicMock(
        return_value=discord.Embed(title="Status")
    )
    return Accounts(poliswag)


def make_ctx(guild=True):
    ctx = MagicMock()
    ctx.guild = MagicMock() if guild else None
    ctx.message.delete = AsyncMock()
    ctx.send = AsyncMock()
    return ctx


class TestAccountReportCmd:
    async def test_happy_path_in_guild_deletes_and_sends_embed(self, cog):
        ctx = make_ctx(guild=True)
        await Accounts.account_report_cmd.callback(cog, ctx)
        ctx.message.delete.assert_awaited_once()
        ctx.send.assert_awaited_once()
        assert (
            ctx.send.call_args.kwargs["embed"]
            is cog.poliswag.account_monitor.build_status_embed.return_value
        )

    async def test_dm_channel_does_not_delete_message(self, cog):
        ctx = make_ctx(guild=False)
        await Accounts.account_report_cmd.callback(cog, ctx)
        ctx.message.delete.assert_not_called()
        ctx.send.assert_awaited_once()

    async def test_outer_exception_logs_and_sends_generic_error(self, cog):
        cog.poliswag.account_monitor.get_account_stats = AsyncMock(
            side_effect=RuntimeError("db down")
        )
        ctx = make_ctx(guild=False)
        await Accounts.account_report_cmd.callback(cog, ctx)
        cog.poliswag.utility.log_to_file.assert_called()
        ctx.send.assert_awaited_once()
        embed = ctx.send.call_args.kwargs["embed"]
        assert "Ocorreu um erro" in embed.title


class TestLifecycle:
    async def test_cog_load_prints(self, cog, capsys):
        await cog.cog_load()
        assert "Accounts loaded" in capsys.readouterr().out

    async def test_cog_unload_prints(self, cog, capsys):
        await cog.cog_unload()
        assert "Accounts unloaded" in capsys.readouterr().out


class TestSetup:
    async def test_registers_cog_on_poliswag(self):
        poliswag = MagicMock()
        poliswag.add_cog = AsyncMock()
        await setup(poliswag)
        poliswag.add_cog.assert_awaited_once()
        assert isinstance(poliswag.add_cog.call_args.args[0], Accounts)
