"""Trade DMs: the message, and the tick's bookkeeping (first run, notices,
closed DMs, the per-tick cap). The matching SQL itself was checked against a
real schema; here the database is faked."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

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


class TestLabel:
    def test_names_the_form(self):
        assert (
            trade_dm.label({**give(), "form_name": "Fall 2019"})
            == "Pikachu (Fall 2019) \u00b7 Shiny"
        )

    def test_falls_back_to_the_dex_number(self):
        assert trade_dm.label(give(pokemon_id=999, name=None)) == "#999 \u00b7 Shiny"

    def test_leaves_a_plain_pokemon_plain(self):
        assert trade_dm.label(give(category="normal")) == "Pikachu"


def dm_db(cutoff="2026-10-16 10:00:00", watermark="w", rows=None):
    db = MagicMock()
    cursor = db.cursor.return_value.__enter__.return_value
    cursor.fetchone.side_effect = [
        {"cutoff": cutoff},
        {"last_trade_dm_at": watermark} if watermark != "missing" else None,
    ]
    cursor.fetchall.return_value = rows or []
    return db, cursor


class TestReadBatch:
    def test_first_run_sets_a_watermark_and_sends_nothing(self):
        sender = TradeDM(MagicMock())
        db, cursor = dm_db(watermark=None)
        sender._connect = MagicMock(return_value=db)
        batch = sender._read_batch()
        assert batch == {
            "cutoff": "2026-10-16 10:00:00",
            "initialise": True,
            "rows": [],
        }
        assert cursor.execute.call_count == 2

    def test_no_settings_row_reads_like_no_watermark(self):
        sender = TradeDM(MagicMock())
        db, _ = dm_db(watermark="missing")
        sender._connect = MagicMock(return_value=db)
        assert sender._read_batch()["initialise"] is True

    def test_rows_since_the_watermark(self):
        sender = TradeDM(MagicMock())
        db, cursor = dm_db(rows=[give()])
        sender._connect = MagicMock(return_value=db)
        batch = sender._read_batch()
        assert batch["initialise"] is False
        assert batch["rows"] == [give()]
        assert cursor.execute.call_args.args[1] == ("w", "2026-10-16 10:00:00")
        db.rollback.assert_called_once()
        db.close.assert_called_once()


class TestHolders:
    """Per holder, in order: their spares for the recipient, and the give-back."""

    def test_groups_gives_and_fetches_what_to_give_back(self):
        sender = TradeDM(MagicMock())
        db = MagicMock()
        cursor = db.cursor.return_value.__enter__.return_value
        cursor.fetchall.side_effect = [[give(133, "Eevee")], []]
        sender._connect = MagicMock(return_value=db)
        rows = [
            give(holder_id=1),
            give(143, "Snorlax", holder_id=1),
            give(holder_id=7),
        ]
        holders = sender._holders("2", rows)
        assert [
            (holder[0], holder[1], len(holder[2]), len(holder[3])) for holder in holders
        ] == [
            ("1", "Rita", 2, 1),
            ("7", "Rita", 1, 0),
        ]
        first, second = cursor.execute.call_args_list
        assert first.args[1] == ("1", "2")
        assert second.args[1] == ("7", "2")
        db.rollback.assert_called_once()
        db.close.assert_called_once()


class TestRecordAndWatermark:
    def test_notices_are_recorded_per_spare(self):
        sender = TradeDM(MagicMock())
        db = MagicMock()
        cursor = db.cursor.return_value.__enter__.return_value
        sender._connect = MagicMock(return_value=db)
        sender._record_notices([give(), give(133, "Eevee")])
        assert cursor.execute.call_count == 2
        first = cursor.execute.call_args_list[0]
        assert "INSERT IGNORE INTO trade_dm_notice" in first.args[0]
        assert first.args[1] == (2, 1, 25, 0, "shiny")
        db.commit.assert_called_once()
        db.close.assert_called_once()

    def test_the_watermark_advances(self):
        sender = TradeDM(MagicMock())
        db = MagicMock()
        cursor = db.cursor.return_value.__enter__.return_value
        sender._connect = MagicMock(return_value=db)
        sender._advance_watermark("2026-10-16 10:00:00")
        assert cursor.execute.call_args.args == (
            "UPDATE poliswag.poliswag SET last_trade_dm_at = %s",
            ("2026-10-16 10:00:00",),
        )
        db.commit.assert_called_once()
        db.close.assert_called_once()


class TestConnect:
    def test_connect_opens_the_trades_database(self):
        db = MagicMock()
        with patch("modules.trade_dm.connect", return_value=db) as connect:
            assert TradeDM(MagicMock())._connect() is db
        connect.assert_called_once_with(
            trade_dm.Config.DB_POGOLEIRIA, dict_rows=True, autocommit=False
        )
