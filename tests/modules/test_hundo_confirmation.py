import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from modules import hundo_confirmation as delivery


@pytest.fixture
def setup(monkeypatch):
    bot = MagicMock()
    bot.user.id = 99
    channel = MagicMock(id=10)
    bot.get_user.return_value.create_dm = AsyncMock(return_value=channel)
    bot.http.request = AsyncMock(return_value={"id": "555"})
    notice = {
        "nonce": "n" * 24,
        "status": "sending",
        "age": 180,
        "started_at": datetime(2026, 9, 25),
        "channel_id": 10,
    }
    claim = MagicMock(return_value=(True, notice))
    finish = MagicMock()
    monkeypatch.setattr(delivery, "claim", claim)
    monkeypatch.setattr(delivery, "finish", finish)
    return bot, channel, notice, claim, finish


async def history(*messages):
    for message in messages:
        yield message


async def test_persists_attempt_before_sending_and_enforces_nonce(setup):
    bot, _, notice, claim, finish = setup

    async def send(*args, **kwargs):
        claim.assert_called_once_with(1, 2, 10)
        assert kwargs["json"]["enforce_nonce"] is True
        assert kwargs["json"]["nonce"] == notice["nonce"]
        assert kwargs["json"]["allowed_mentions"] == {"parse": []}
        return {"id": "555"}

    bot.http.request.side_effect = send
    assert (
        await delivery.send_once(
            bot, {"discord_id": 1, "hundo_settings_revision": 2}, discord.Embed()
        )
        is True
    )
    finish.assert_called_once_with(1, 2, "sent", "555")


async def test_restart_after_accepted_send_recovers_history_without_sending(setup):
    bot, channel, notice, claim, finish = setup
    finish.side_effect = RuntimeError(
        "DB acknowledgement failed after Discord accepted"
    )
    row = {"discord_id": 1, "hundo_settings_revision": 2}
    with pytest.raises(RuntimeError):
        await delivery.send_once(bot, row, discord.Embed())
    # Durable sending row survives, unlike any HundoAlerts instance memory.
    claim.return_value = (False, notice)
    finish.side_effect = None
    channel.history.return_value = history(
        MagicMock(id=555, nonce=notice["nonce"], author=MagicMock(id=99))
    )
    assert await delivery.send_once(bot, row, discord.Embed()) is True
    bot.http.request.assert_awaited_once()
    finish.assert_called_with(1, 2, "sent", 555)


async def test_uncertain_delivery_never_blindly_resends(setup):
    bot, channel, notice, claim, finish = setup
    claim.return_value = (False, notice)
    channel.history.side_effect = lambda **kw: history()
    row = {"discord_id": 1, "hundo_settings_revision": 2}
    for _ in range(2):
        assert await delivery.send_once(bot, row, discord.Embed()) is None
    bot.http.request.assert_not_awaited()
    finish.assert_called_with(1, 2, "uncertain")


async def test_in_flight_attempt_is_not_recovered_prematurely(setup):
    bot, channel, notice, claim, finish = setup
    claim.return_value = (False, {**notice, "age": 30})
    assert (
        await delivery.send_once(
            bot, {"discord_id": 1, "hundo_settings_revision": 2}, discord.Embed()
        )
        is None
    )
    channel.history.assert_not_called()
    bot.http.request.assert_not_awaited()
    finish.assert_not_called()


@pytest.mark.parametrize("status,result", [("sent", True), ("refused", False)])
async def test_known_outcomes_survive_restart(setup, status, result):
    bot, _, notice, claim, _ = setup
    claim.return_value = (False, {**notice, "status": status})
    assert (
        await delivery.send_once(
            bot, {"discord_id": 1, "hundo_settings_revision": 2}, discord.Embed()
        )
        is result
    )
    bot.http.request.assert_not_awaited()


async def test_failed_claim_never_sends(setup):
    bot, _, _, claim, _ = setup
    claim.side_effect = RuntimeError("DB unavailable")
    with pytest.raises(RuntimeError):
        await delivery.send_once(
            bot, {"discord_id": 1, "hundo_settings_revision": 2}, discord.Embed()
        )
    bot.http.request.assert_not_awaited()


async def test_timeout_leaves_attempt_for_recovery(setup):
    bot, _, _, _, finish = setup
    bot.http.request.side_effect = asyncio.TimeoutError()
    with pytest.raises(TimeoutError):
        await delivery.send_once(
            bot, {"discord_id": 1, "hundo_settings_revision": 2}, discord.Embed()
        )
    finish.assert_not_called()
