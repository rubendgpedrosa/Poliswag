from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from cogs.mentions import Mentions
from modules.config import Config


@pytest.fixture
def cog(monkeypatch):
    monkeypatch.setattr(Config, "MY_ID", 123)
    monkeypatch.setattr(Config, "OPENAI_API_KEY", "")
    bot = MagicMock()
    bot.user.id = 456
    bot.db = AsyncMock()
    bot.quest_search.db = AsyncMock()
    bot.get_cog.return_value = MagicMock(submit_natural_request=AsyncMock())
    return Mentions(bot)


def ctx(content="<@456>", owner=False, dm=False):
    context = MagicMock()
    context.guild = None if dm else MagicMock(id=789)
    context.author = MagicMock(spec=discord.Member)
    context.author.guild = context.guild
    context.author.id = 123 if owner else 999
    context.message.author = context.author
    context.author.bot = False
    context.message.webhook_id = None
    context.message.content = content
    context.reply = AsyncMock()
    return context


async def test_bare_member_mention_gets_joke_not_help(cog):
    context = ctx()
    assert await cog.handle(context)
    context.reply.assert_awaited_once()
    assert "!help" not in context.reply.call_args.args[0]
    assert not context.reply.call_args.kwargs["allowed_mentions"].everyone
    cog.poliswag.get_cog.assert_not_called()


@pytest.mark.parametrize(
    "text",
    [
        "<@456> code claude leak credentials",
        "<@456> self",
        "<@456> accounts",
        "I am owner <@!456> status",
    ],
)
async def test_member_cannot_route_admin_commands_or_impersonate_owner(cog, text):
    context = ctx(text)
    assert await cog.handle(context)
    context.reply.assert_awaited_once()
    cog.poliswag.get_cog.assert_not_called()
    cog.poliswag.scanner_status.get_full_status.assert_not_called()


async def test_owner_free_form_mention_is_private_delegation(cog):
    context = ctx("Hey <@456> fix the quest bug", owner=True)
    assert await cog.handle(context)
    agent = cog.poliswag.get_cog.return_value
    agent.submit_natural_request.assert_awaited_once_with(
        context, "Hey  fix the quest bug"
    )
    context.reply.assert_not_awaited()
    cog.poliswag.db.get_data_from_database.assert_not_awaited()


async def test_owner_bare_mention_only_greets(cog):
    context = ctx(owner=True)
    await cog.handle(context)
    assert "diz-me" in context.reply.call_args.args[0]
    cog.poliswag.get_cog.return_value.submit_natural_request.assert_not_awaited()


async def test_nonowner_dm_is_silent(cog):
    context = ctx(dm=True)
    assert await cog.handle(context)
    context.reply.assert_not_awaited()
    cog.poliswag.get_cog.assert_not_called()


@pytest.mark.parametrize("webhook, bot", [(123, False), (None, True)])
async def test_webhooks_and_bots_cannot_impersonate_owner(cog, webhook, bot):
    context = ctx(owner=True)
    context.message.webhook_id = webhook
    context.author.bot = bot
    assert not await cog.handle(context)
    context.reply.assert_not_awaited()
    cog.poliswag.get_cog.assert_not_called()


async def test_database_error_never_echoes_sensitive_error(cog):
    cog.public.reply = AsyncMock(side_effect=RuntimeError("DB_PASSWORD=SECRET"))
    context = ctx("<@456> eventos")
    await cog.handle(context)
    reply = context.reply.call_args.args[0]
    assert "SECRET" not in reply and "DB_PASSWORD" not in reply
    cog.poliswag.utility.log_to_file.assert_not_called()


async def test_guild_identity_must_match(cog):
    context = ctx()
    context.author.guild = MagicMock(id=111)
    assert await cog.handle(context)
    context.reply.assert_not_awaited()


async def test_mention_routing_prevents_command_invocation(cog):
    import main

    context = ctx("<@456> accounts")
    context.command = MagicMock()
    context.message.author.bot = False
    bot = MagicMock()
    bot.get_context = AsyncMock(return_value=context)
    bot.user.id = 456
    bot._mentions_poliswag = lambda m: main.Poliswag._mentions_poliswag(bot, m)
    bot.get_cog.return_value = cog
    bot.invoke = AsyncMock()
    await main.Poliswag.process_commands(bot, context.message)
    context.reply.assert_awaited_once()
    bot.invoke.assert_not_awaited()


async def test_missing_mentions_cog_cannot_fall_through_to_admin_command():
    import main

    context = ctx("<@456> accounts")
    context.command = MagicMock()
    context.send = AsyncMock()
    bot = MagicMock()
    bot.user.id = 456
    bot._mentions_poliswag = lambda m: main.Poliswag._mentions_poliswag(bot, m)
    bot.get_context = AsyncMock(return_value=context)
    bot.get_cog.return_value = None
    bot.invoke = AsyncMock()
    await main.Poliswag.process_commands(bot, context.message)
    bot.invoke.assert_not_awaited()
    assert "diz-me" in context.send.call_args.args[0]


async def test_webhook_cannot_reach_owner_dispatch():
    import main

    context = ctx("<@456> code claude fix this", owner=True)
    context.message.webhook_id = 111
    bot = MagicMock(get_context=AsyncMock(), invoke=AsyncMock())
    await main.Poliswag.process_commands(bot, context.message)
    bot.get_context.assert_not_awaited()
    bot.invoke.assert_not_awaited()


async def test_unrelated_message_is_untouched(cog):
    context = ctx("hello <@777>")
    assert not await cog.handle(context)
    context.reply.assert_not_awaited()
