"""Tests for cogs.container_manager.ContainerManagerCog.

The module reads ``MY_ID`` and ``SCANNER_CONTAINER_NAME`` from env at import
time, so we seed them before the import. Command callbacks are invoked
directly; error handlers are called with synthetic ``commands.CheckFailure``
and generic Exception instances.
"""

import os

os.environ.setdefault("MY_ID", "111")
os.environ.setdefault("SCANNER_CONTAINER_NAME", "test-scanner")

from unittest.mock import AsyncMock, MagicMock  # noqa: E402

import pytest  # noqa: E402
from discord.ext import commands  # noqa: E402

from cogs.container_manager import ContainerManagerCog, OWNER_ID  # noqa: E402


@pytest.fixture
def cog():
    poliswag = MagicMock()
    return ContainerManagerCog(poliswag)


def make_ctx(author_id=OWNER_ID):
    ctx = MagicMock()
    ctx.author.id = author_id
    ctx.send = AsyncMock()
    return ctx


class TestCogCheck:
    def test_owner_passes(self, cog):
        assert cog.cog_check(make_ctx(author_id=OWNER_ID)) is True

    def test_non_owner_rejected(self, cog):
        assert cog.cog_check(make_ctx(author_id=999)) is False


class TestContainerGroup:
    async def test_fallback_invocation_sends_help(self, cog):
        ctx = make_ctx()
        await ContainerManagerCog.container.callback(cog, ctx)
        ctx.send.assert_awaited_once()
        assert "Invalid container command" in ctx.send.call_args.args[0]


class TestStartContainer:
    async def test_success_path_sends_two_messages(self, cog):
        ctx = make_ctx()
        await ContainerManagerCog.start_container.callback(cog, ctx)
        cog.poliswag.scanner_manager.change_scanner_status.assert_called_once_with(
            "start"
        )
        assert ctx.send.await_count == 2

    async def test_exception_logs_and_sends_error(self, cog):
        ctx = make_ctx()
        cog.poliswag.scanner_manager.change_scanner_status.side_effect = RuntimeError(
            "docker offline"
        )
        await ContainerManagerCog.start_container.callback(cog, ctx)
        cog.poliswag.utility.log_to_file.assert_called()
        # Attempt-message + error-message = 2 sends
        assert ctx.send.await_count == 2


class TestStopContainer:
    async def test_success_path_sends_two_messages(self, cog):
        ctx = make_ctx()
        await ContainerManagerCog.stop_container.callback(cog, ctx)
        cog.poliswag.scanner_manager.change_scanner_status.assert_called_once_with(
            "stop"
        )
        assert ctx.send.await_count == 2

    async def test_exception_logs_and_sends_error(self, cog):
        ctx = make_ctx()
        cog.poliswag.scanner_manager.change_scanner_status.side_effect = RuntimeError(
            "docker offline"
        )
        await ContainerManagerCog.stop_container.callback(cog, ctx)
        cog.poliswag.utility.log_to_file.assert_called()
        assert ctx.send.await_count == 2


class TestContainerErrorHandler:
    async def test_check_failure_sends_unauthorized(self, cog):
        ctx = make_ctx()
        err = commands.CheckFailure("no")
        await cog.container_error(ctx, err)
        ctx.send.assert_awaited_once_with("You are not authorized to use this command.")

    async def test_command_not_found_sends_help(self, cog):
        ctx = make_ctx()
        err = commands.CommandNotFound("huh")
        await cog.container_error(ctx, err)
        assert "Invalid container command" in ctx.send.call_args.args[0]

    async def test_other_error_logs_and_sends(self, cog):
        ctx = make_ctx()
        err = RuntimeError("explosion")
        await cog.container_error(ctx, err)
        cog.poliswag.utility.log_to_file.assert_called()
        ctx.send.assert_awaited_once()


class TestStartStopContainerErrorHandlers:
    async def test_start_check_failure(self, cog):
        ctx = make_ctx()
        err = commands.CheckFailure("no")
        await cog.start_container_error(ctx, err)
        ctx.send.assert_awaited_once_with("You are not authorized to use this command.")

    async def test_start_other_error(self, cog):
        ctx = make_ctx()
        err = RuntimeError("kaboom")
        await cog.start_container_error(ctx, err)
        cog.poliswag.utility.log_to_file.assert_called()

    async def test_stop_check_failure(self, cog):
        ctx = make_ctx()
        err = commands.CheckFailure("no")
        await cog.stop_container_error(ctx, err)
        ctx.send.assert_awaited_once_with("You are not authorized to use this command.")

    async def test_stop_other_error(self, cog):
        ctx = make_ctx()
        err = RuntimeError("kaboom")
        await cog.stop_container_error(ctx, err)
        cog.poliswag.utility.log_to_file.assert_called()


class TestLifecycle:
    async def test_cog_load_prints(self, cog, capsys):
        await cog.cog_load()
        assert "ContainerManagerCog loaded" in capsys.readouterr().out

    async def test_cog_unload_prints(self, cog, capsys):
        await cog.cog_unload()
        assert "ContainerManagerCog unloaded" in capsys.readouterr().out
