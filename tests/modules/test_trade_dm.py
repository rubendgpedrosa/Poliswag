"""Trade DMs: the message, and the tick's bookkeeping (first run, notices,
closed DMs, the per-tick cap). The matching SQL itself was checked against a
real schema; here the database is faked."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import discord

from modules import trade_dm
from modules.trade_dm import TradeDM, message


def give(
    pokemon_id=25,
    name="Pikachu",
    category="shiny",
    holder_id=1,
    recipient_id=2,
    holder_name="Rita",
):
    return {
        "holder_id": holder_id,
        "recipient_id": recipient_id,
        "pokemon_id": pokemon_id,
        "form_id": 0,
        "category": category,
        "holder_name": holder_name,
        "pokemon_name": name,
        "form_name": None,
    }


class TestMessage:
    def test_one_trainer(self):
        text = message([("1", "Rita", [give()], [give(133, "Eevee")])])
        assert text.startswith("🔄 Tens uma troca nova com **Rita**")
        assert "Tem para ti: Pikachu · Shiny" in text
        assert "Quer de ti: Eevee · Shiny" in text
        assert "/jogador/1?o=pokedex-dm>" in text
        assert "Perfil e outras opções" in text

    def test_several_trainers_are_named_and_capped(self):
        holders = [(str(i), f"T{i}", [give(holder_id=i)], []) for i in range(7)]
        text = message(holders)
        assert text.startswith("🔄 Tens 7 trocas novas")
        assert "**T0**" in text and "**T4**" in text and "**T5**" not in text
        assert "E mais 2:" in text

    def test_long_lines_say_how_many_more(self):
        gives = [give(i, f"P{i}") for i in range(1, 7)]
        assert "(+2)" in message([("1", "Rita", gives, [])])


def dm(rows, initialise=False):
    bot = MagicMock()
    user = MagicMock()
    user.send = AsyncMock()
    bot.get_user.return_value = user
    sender = TradeDM(bot)
    sender._read_batch = MagicMock(
        return_value={"cutoff": "c", "initialise": initialise, "rows": rows}
    )
    sender._holders = MagicMock(
        side_effect=lambda _recipient, rs: [
            (str(rs[0]["holder_id"]), rs[0]["holder_name"], rs, [])
        ]
    )
    sender._record_notices = MagicMock()
    sender._advance_watermark = MagicMock()
    return sender, user


class TestTick:
    def test_first_run_sends_nothing_and_sets_the_watermark(self):
        sender, user = dm([], initialise=True)
        asyncio.run(sender.tick())
        user.send.assert_not_called()
        sender._advance_watermark.assert_called_once_with("c")

    def test_one_dm_per_recipient_recorded_then_watermark(self):
        rows = [
            give(recipient_id=2),
            give(133, "Eevee", recipient_id=2),
            give(recipient_id=3),
        ]
        sender, user = dm(rows)
        asyncio.run(sender.tick())
        assert user.send.await_count == 2
        assert sender._record_notices.call_count == 2
        sender._advance_watermark.assert_called_once_with("c")

    def test_closed_dms_are_recorded_so_they_are_not_retried(self):
        sender, user = dm([give()])
        user.send.side_effect = discord.Forbidden(MagicMock(status=403), "closed")
        asyncio.run(sender.tick())
        sender._record_notices.assert_called_once()
        sender._advance_watermark.assert_called_once()

    def test_past_the_cap_the_watermark_waits(self):
        rows = [give(recipient_id=r) for r in range(trade_dm._RECIPIENTS_PER_TICK + 1)]
        sender, user = dm(rows)
        asyncio.run(sender.tick())
        assert user.send.await_count == trade_dm._RECIPIENTS_PER_TICK
        sender._advance_watermark.assert_not_called()
