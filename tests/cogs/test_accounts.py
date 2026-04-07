"""Tests for cogs.accounts.Accounts.

The cog is a thin delegator over poliswag.account_monitor and
poliswag.image_generator. We invoke command callbacks directly via
``Accounts.account_report_cmd.callback(cog, ctx)`` to bypass discord's
dispatcher and test the error-handling branches in isolation.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from cogs.accounts import Accounts


@pytest.fixture
def cog():
    poliswag = MagicMock()
    poliswag.account_monitor.get_account_stats = AsyncMock(return_value={"ok": 1})
    poliswag.account_monitor.is_device_connected = AsyncMock(return_value=True)
    poliswag.image_generator.generate_image_from_account_stats = AsyncMock(
        return_value=b"PNGBYTES"
    )
    return Accounts(poliswag)


def make_ctx(guild=True):
    ctx = MagicMock()
    ctx.guild = MagicMock() if guild else None
    ctx.message.delete = AsyncMock()
    ctx.send = AsyncMock()
    return ctx


class TestAccountReportCmd:
    async def test_happy_path_in_guild_deletes_and_sends_file(self, cog):
        ctx = make_ctx(guild=True)
        await Accounts.account_report_cmd.callback(cog, ctx)
        ctx.message.delete.assert_awaited_once()
        ctx.send.assert_awaited_once()
        # The only send call must carry a discord.File
        _, kwargs = ctx.send.call_args
        assert "file" in kwargs

    async def test_dm_channel_does_not_delete_message(self, cog):
        ctx = make_ctx(guild=False)
        await Accounts.account_report_cmd.callback(cog, ctx)
        ctx.message.delete.assert_not_called()
        ctx.send.assert_awaited_once()

    async def test_no_image_bytes_sends_error_text(self, cog):
        cog.poliswag.image_generator.generate_image_from_account_stats = AsyncMock(
            return_value=None
        )
        ctx = make_ctx(guild=False)
        await Accounts.account_report_cmd.callback(cog, ctx)
        ctx.send.assert_awaited_once_with("Error generating account image. Check logs.")

    async def test_send_file_failure_logs_and_sends_error_text(self, cog, mocker):
        ctx = make_ctx(guild=False)
        # First send (the File) raises, second send (error text) succeeds.
        ctx.send = AsyncMock(side_effect=[Exception("boom"), None])
        await Accounts.account_report_cmd.callback(cog, ctx)
        cog.poliswag.utility.log_to_file.assert_called()
        assert ctx.send.await_count == 2

    async def test_outer_exception_logs_and_sends_generic_error(self, cog):
        cog.poliswag.account_monitor.get_account_stats = AsyncMock(
            side_effect=RuntimeError("db down")
        )
        ctx = make_ctx(guild=False)
        await Accounts.account_report_cmd.callback(cog, ctx)
        cog.poliswag.utility.log_to_file.assert_called()
        ctx.send.assert_awaited_once()
        assert "An error occurred" in ctx.send.call_args.args[0]


class TestLifecycle:
    async def test_cog_load_prints(self, cog, capsys):
        await cog.cog_load()
        assert "Accounts loaded" in capsys.readouterr().out

    async def test_cog_unload_prints(self, cog, capsys):
        await cog.cog_unload()
        assert "Accounts unloaded" in capsys.readouterr().out
