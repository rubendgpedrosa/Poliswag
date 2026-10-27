from unittest.mock import AsyncMock, MagicMock

import pytest

from cogs.claude_chat import ClaudeChat
from cogs.hermes import Hermes, RunProgress
from modules.config import Config
from tests.cogs.test_hermes import context


@pytest.fixture
def cogs(monkeypatch):
    monkeypatch.setattr(Config, "MY_ID", 123)
    bot = MagicMock()
    hermes, claude = Hermes(bot), ClaudeChat(bot)
    hermes.client.reply = AsyncMock(return_value=("run-1", "Hermes"))
    claude.client.reply = AsyncMock(return_value=("run-2", "Claude"))
    return hermes, claude


async def route(cogs, text, *, dm=True, owner=True, command=False):
    from main import Poliswag

    hermes, claude = cogs
    ctx = context(owner=owner, dm=dm)
    ctx.message.content = text
    ctx.command = MagicMock() if command else None
    bot = MagicMock()
    bot.get_context = AsyncMock(return_value=ctx)
    bot._mentions_poliswag.return_value = False
    bot.get_cog.side_effect = {"Hermes": hermes, "ClaudeChat": claude}.get
    bot.invoke = AsyncMock()
    await Poliswag.process_commands(bot, ctx.message)
    return ctx


@pytest.mark.parametrize(
    "text,sent",
    [
        ("c vê o scanner", "vê o scanner"),
        ("!c vê o scanner", "vê o scanner"),
        ("  C   linha 1\nlinha 2", "linha 1\nlinha 2"),
    ],
)
async def test_prefix_sends_only_the_request_to_claude(cogs, text, sent):
    hermes, claude = cogs
    await route(cogs, text)
    assert claude.client.reply.await_args.args[0] == sent
    hermes.client.reply.assert_not_awaited()


@pytest.mark.parametrize("text", ["cá está", "claude?", "olá c", "c'mon"])
async def test_other_dms_stay_with_hermes(cogs, text):
    hermes, claude = cogs
    await route(cogs, text)
    hermes.client.reply.assert_awaited_once()
    claude.client.reply.assert_not_awaited()


@pytest.mark.parametrize("kwargs", [{"owner": False}, {"dm": False}, {"command": True}])
async def test_prefix_needs_owner_dm_without_command(cogs, kwargs):
    _, claude = cogs
    await route(cogs, "c olá", **kwargs)
    claude.client.reply.assert_not_awaited()


async def test_bare_prefix_explains_usage(cogs):
    _, claude = cogs
    ctx = await route(cogs, "!c")
    claude.client.reply.assert_not_awaited()
    assert "depois de `c `" in ctx.reply.await_args.args[0]


async def test_claude_uses_its_own_bridge_and_labels(cogs):
    hermes, claude = cogs
    assert claude.client.url == "http://claude.test.invalid"
    assert claude.client.runs_dir == "/tmp/poliswag-test/claude-runs"
    assert hermes.client.runs_dir != claude.client.runs_dir
    assert RunProgress(name=claude.NAME).embed().title == "⚙️ Claude a trabalhar"
