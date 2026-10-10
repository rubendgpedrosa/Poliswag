from unittest.mock import AsyncMock, MagicMock

import pytest

from cogs.agents import Agents
from modules.config import Config
from modules.permissions import is_owner


@pytest.fixture
def cog(tmp_path, monkeypatch):
    monkeypatch.setattr(Config, "MY_ID", 123)
    monkeypatch.setattr(Config, "AGENT_JOBS_DIR", str(tmp_path / "jobs"))
    bot = MagicMock()
    bot.user.id = 456
    return Agents(bot)


def ctx(author=123, content="claude explain", guild=None):
    context = MagicMock()
    context.author.id = author
    context.guild = guild
    context.message.content = content
    return context


@pytest.mark.parametrize("author", [999, 42])
async def test_nonowner_direct_request_never_enqueued(cog, author):
    cog.submit = AsyncMock()
    assert not await cog.handle_direct_request(ctx(author=author))
    cog.submit.assert_not_awaited()


async def test_owner_dm_routes_explicit_code_request(cog):
    cog.submit = AsyncMock()
    context = ctx(content="code openai fix this")
    assert await cog.handle_direct_request(context)
    cog.submit.assert_awaited_once_with(context, "code", "openai", "fix this")


async def test_channel_requires_leading_bot_mention(cog):
    cog.submit = AsyncMock()
    assert not await cog.handle_direct_request(ctx(guild=MagicMock()))
    context = ctx(content="<@456> claude explain", guild=MagicMock())
    assert await cog.handle_direct_request(context)
    cog.submit.assert_awaited_once_with(context, "ask", "claude", "explain")


async def test_closed_dm_does_not_launch_work(cog):
    import discord

    context = ctx()
    context.send = AsyncMock()
    context.author.create_dm = AsyncMock(
        side_effect=discord.Forbidden(MagicMock(status=403), "blocked")
    )
    cog.jobs.submit = MagicMock()
    await cog.submit(context, "code", "claude", "fix")
    cog.jobs.submit.assert_not_called()


def test_every_agent_command_has_owner_gate(cog):
    assert cog.cog_check(ctx())
    assert not cog.cog_check(ctx(author=999))
    assert all(is_owner in command.checks for command in cog.get_commands())


async def test_result_is_private_and_mentions_disabled(cog):
    from modules.agent_jobs import write_json

    directory = cog.jobs.directory("abcdef123456")
    directory.mkdir(parents=True)
    write_json(directory / "request.json", {})
    (directory / "answer.txt").write_text("@everyone " + "x" * 4000)
    user = MagicMock(send=AsyncMock())
    await cog.send_result(
        user,
        {"id": directory.name, "provider": "claude", "state": "completed"},
    )
    sent = user.send.call_args.kwargs
    assert not sent["allowed_mentions"].everyone
    assert "file" in sent


async def test_delivery_marker_prevents_repeated_completion_dm(cog):
    from modules.agent_jobs import write_json

    directory = cog.jobs.directory("abcdef123456")
    directory.mkdir(parents=True)
    write_json(
        directory / "request.json",
        {"id": directory.name, "owner_id": "123", "created": 1, "provider": "claude"},
    )
    write_json(directory / "result.json", {"state": "completed"})
    cog.send_result = AsyncMock()
    await cog.deliver_results()
    await cog.deliver_results()
    cog.send_result.assert_awaited_once()
