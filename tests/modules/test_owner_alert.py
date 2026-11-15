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


async def test_incident_is_one_message_edited_on_recovery(bot, mocker, tmp_path):
    # Owner, 2026-11-15: "Mapa sem dados" becomes "Mapa recuperado" in place.
    mocker.patch.object(owner_alert.Config, "STATE_DIR", str(tmp_path))
    user = bot.get_user.return_value
    user.send = AsyncMock(return_value=MagicMock(id=555))
    partial = MagicMock(edit=AsyncMock())
    user.dm_channel.get_partial_message.return_value = partial
    assert await owner_alert.notify_owner(
        bot, "down", title="🔴 Mapa sem dados", tag="map", incident="map"
    )
    assert await owner_alert.notify_owner(
        bot,
        "back",
        title="✅ Mapa recuperado",
        tag="map",
        incident="map",
        resolved=True,
    )
    user.send.assert_awaited_once()
    user.dm_channel.get_partial_message.assert_called_once_with(555)
    embed = partial.edit.await_args.kwargs["embed"]
    assert embed.title == "✅ Mapa recuperado"
    assert "Durou 1 min." in embed.description
    assert embed.colour.value == owner_alert.RECOVERED_COLOR


async def test_recovery_is_sent_anew_when_the_message_is_gone(bot, mocker, tmp_path):
    mocker.patch.object(owner_alert.Config, "STATE_DIR", str(tmp_path))
    user = bot.get_user.return_value
    user.send = AsyncMock(return_value=MagicMock(id=555))
    partial = MagicMock(
        edit=AsyncMock(side_effect=discord.NotFound(MagicMock(status=404), "x"))
    )
    user.dm_channel.get_partial_message.return_value = partial
    await owner_alert.notify_owner(bot, "down", title="D", tag="m", incident="map")
    assert await owner_alert.notify_owner(
        bot, "back", title="R", tag="m", incident="map", resolved=True
    )
    assert user.send.await_count == 2
    assert user.send.await_args.kwargs["embed"].title == "R"
