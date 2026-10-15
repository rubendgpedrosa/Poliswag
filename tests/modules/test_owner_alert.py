"""Owner operational alerts go directly to Discord, including fetch failures."""

from unittest.mock import AsyncMock, MagicMock

import aiohttp
import discord
import pytest

from modules import owner_alert


@pytest.fixture
def bot(mocker):
    mocker.patch.object(owner_alert.Config, "MY_ID", 123)
    bot = MagicMock()
    bot.get_user.return_value.send = AsyncMock()
    bot.fetch_user = AsyncMock()
    return bot


@pytest.mark.parametrize("production", [True, False])
async def test_direct_discord_preserves_embed_and_never_calls_ha(
    bot, mocker, production
):
    mocker.patch.object(owner_alert.Config, "IS_PRODUCTION", production)
    http = mocker.patch("modules.http_client.get_session")
    assert await owner_alert.notify_owner(bot, "**down**", title="T", tag="tracking")
    http.assert_not_called()
    embed = bot.get_user.return_value.send.await_args.kwargs["embed"]
    assert (embed.title, embed.description) == ("T", "**down**")
    bot.fetch_user.assert_not_called()


async def test_fetches_owner_when_not_cached(bot):
    bot.get_user.return_value = None
    bot.fetch_user.return_value.send = AsyncMock()
    assert await owner_alert.notify_owner(bot, "down", title="T", tag="t")
    bot.fetch_user.assert_awaited_once_with(123)
    bot.fetch_user.return_value.send.assert_awaited_once()


@pytest.mark.parametrize("stage", ["fetch", "send"])
@pytest.mark.parametrize(
    "error",
    [
        discord.HTTPException(MagicMock(status=403), "no"),
        aiohttp.ClientConnectionError(),
        TimeoutError(),
        OSError(),
    ],
)
async def test_delivery_failures_return_false_and_log(bot, stage, error):
    if stage == "fetch":
        bot.get_user.return_value = None
        bot.fetch_user.side_effect = error
    else:
        bot.get_user.return_value.send.side_effect = error
    assert not await owner_alert.notify_owner(bot, "down", title="T", tag="t")
    bot.utility.log_to_file.assert_called_once()
    assert "Discord delivery failed" in bot.utility.log_to_file.call_args.args[0]


async def test_missing_owner_returns_false_and_logs(bot, mocker):
    mocker.patch.object(owner_alert.Config, "MY_ID", 0)
    assert not await owner_alert.notify_owner(bot, "down", title="T", tag="t")
    bot.get_user.assert_not_called()
    bot.utility.log_to_file.assert_called_once()
