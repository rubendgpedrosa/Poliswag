import asyncio
import hashlib
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


def claim_db(rowcount=1, notice="notice"):
    db = MagicMock()
    cursor = db.cursor.return_value.__enter__.return_value
    cursor.rowcount = rowcount
    cursor.fetchone.return_value = notice
    return db, cursor


class TestClaim:
    """The atomic journal write: one INSERT...SELECT, then the row back."""

    def test_winner_owns_the_notice(self, monkeypatch):
        notice = {"status": "sending"}
        db, cursor = claim_db(rowcount=1, notice=notice)
        monkeypatch.setattr(delivery, "connect", MagicMock(return_value=db))
        owned, got = delivery.claim(1, 2, 10)
        assert (owned, got) == (True, notice)
        insert, select = cursor.execute.call_args_list
        assert "INSERT IGNORE INTO hundo_confirmation" in insert.args[0]
        nonce = hashlib.sha256(b"hundo:1:2").hexdigest()[:24]
        assert insert.args[1] == (2, 10, nonce, 1, 2, 2)
        assert select.args[1] == (1, 2)
        db.close.assert_called_once()

    def test_loser_reads_the_winners_notice(self, monkeypatch):
        notice = {"status": "sending"}
        db, _ = claim_db(rowcount=0, notice=notice)
        monkeypatch.setattr(delivery, "connect", MagicMock(return_value=db))
        assert delivery.claim(1, 2, 10) == (False, notice)

    def test_moved_settings_claim_nothing(self, monkeypatch):
        db, _ = claim_db(rowcount=0, notice=None)
        monkeypatch.setattr(delivery, "connect", MagicMock(return_value=db))
        assert delivery.claim(1, 2, 10) == (False, None)


class TestFinish:
    def test_finish_only_flips_sending_or_uncertain_rows(self, monkeypatch):
        db, cursor = claim_db()
        monkeypatch.setattr(delivery, "connect", MagicMock(return_value=db))
        delivery.finish(1, 2, "sent", "555")
        sql, params = cursor.execute.call_args.args
        assert "AND status IN ('sending', 'uncertain')" in sql
        assert params == ("sent", "555", 1, 2)
        db.close.assert_called_once()

    def test_finish_without_a_message_keeps_the_old_one(self, monkeypatch):
        db, cursor = claim_db()
        monkeypatch.setattr(delivery, "connect", MagicMock(return_value=db))
        delivery.finish(1, 2, "uncertain")
        assert cursor.execute.call_args.args[1] == ("uncertain", None, 1, 2)


async def test_settings_changed_before_the_claim_sends_nothing(setup):
    bot, _, _, claim, finish = setup
    claim.return_value = (True, None)
    assert (
        await delivery.send_once(
            bot, {"discord_id": 1, "hundo_settings_revision": 2}, discord.Embed()
        )
        is None
    )
    bot.http.request.assert_not_awaited()
    finish.assert_not_called()


async def test_unreadable_history_stays_uncertain(setup):
    bot, channel, notice, claim, finish = setup
    claim.return_value = (False, notice)
    channel.history.side_effect = discord.HTTPException(MagicMock(), "nope")
    assert (
        await delivery.send_once(
            bot, {"discord_id": 1, "hundo_settings_revision": 2}, discord.Embed()
        )
        is None
    )
    bot.http.request.assert_not_awaited()
    finish.assert_called_with(1, 2, "uncertain")


async def test_closed_dms_are_recorded_as_refused(setup):
    bot, _, _, claim, finish = setup
    bot.http.request.side_effect = discord.Forbidden(MagicMock(), "closed")
    assert (
        await delivery.send_once(
            bot, {"discord_id": 1, "hundo_settings_revision": 2}, discord.Embed()
        )
        is False
    )
    finish.assert_called_once_with(1, 2, "refused")
