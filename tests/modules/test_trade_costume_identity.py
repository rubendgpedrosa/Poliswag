"""Costume-aware SQL matching, independent of live services or Discord."""

import sqlite3
import pytest
from modules import trade_dm


def _match_db():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.create_function(
        "FIND_IN_SET", 2, lambda item, values: int(item in values.split(","))
    )
    db.executescript("""
    ATTACH DATABASE ':memory:' AS poliswag;
    CREATE TABLE poliswag.pokemon_name(pokemon_id,form_id,name,form_name,is_costume);
    CREATE TABLE poliswag.pokemon_catalogue(pokemon_id,form_id,costume_id,name,form_name,is_costume);
    CREATE TABLE trade_player(discord_id,trainer_name,display_name,left_at,trade_dms,collecting,show_costumes);
    CREATE TABLE trade_entry(discord_id,pokemon_id,form_id,costume_id,category,list,trade_for_category,created_at);
    CREATE TABLE collection_entry(discord_id,pokemon_id,form_id,costume_id,category);
    CREATE TABLE trade_dm_notice(recipient_id,holder_id,pokemon_id,form_id,costume_id,category);
    INSERT INTO poliswag.pokemon_name VALUES(25,0,'Pikachu',NULL,0),(1,0,'Bulbasaur',NULL,0);
    INSERT INTO poliswag.pokemon_catalogue VALUES(25,0,0,'Pikachu',NULL,0),(25,0,1,'Pikachu','Santa Hat',1),(25,0,2,'Pikachu','Party Hat',1),(1,0,0,'Bulbasaur',NULL,0);
    INSERT INTO trade_player VALUES(1,'Alice','Alice',NULL,1,'normal',1),(2,'Bob','Bob',NULL,1,'normal',1);
    INSERT INTO trade_entry VALUES(1,25,0,1,'normal','have',NULL,100),(2,1,0,0,'normal','have',NULL,100);
    INSERT INTO collection_entry VALUES(2,25,0,2,'normal');
    INSERT INTO trade_dm_notice VALUES(2,1,25,0,0,'normal');
    """)
    return db


def test_other_hat_and_old_normal_notice_do_not_suppress_a_missing_costume():
    db = _match_db()
    rows = db.execute(
        trade_dm._NEW_MUTUALS_SQL.replace("%s", "?"), (0, 1000)
    ).fetchall()
    exact = [row for row in rows if row["holder_id"] == 1 and row["recipient_id"] == 2]
    assert (
        len(exact) == 1
    ), "A different hat/ordinary notice is not the same collectible"
    assert exact[0]["costume_id"] == 1
    assert exact[0]["form_name"] == "Santa Hat"


def test_notice_records_the_exact_costume_id():
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    cursor = MagicMock()
    db = MagicMock()
    db.cursor.return_value.__enter__.return_value = cursor
    cog = SimpleNamespace(_connect=lambda: db)
    row = {
        "recipient_id": 2,
        "holder_id": 1,
        "pokemon_id": 25,
        "form_id": 0,
        "costume_id": 1,
        "category": "normal",
    }
    trade_dm.TradeDM._record_notices(cog, [row])
    query, params = cursor.execute.call_args.args
    assert "costume_id" in query
    assert params == (2, 1, 25, 0, 1, "normal")


def test_digest_names_the_exact_costume_instead_of_the_plain_species():
    from modules import trade_digest

    assert "e.costume_id" in trade_digest._ROWS_SQL
    assert "pn.costume_id = e.costume_id" in trade_digest._ROWS_SQL
    assert "poliswag.pokemon_catalogue" in trade_digest._ROWS_SQL


def test_digest_passes_costume_to_checked_sprite_helper(monkeypatch):
    from modules import trade_digest
    from unittest.mock import Mock

    art = Mock(return_value="https://example.test/costume.png")
    monkeypatch.setattr(trade_digest, "pokemon_sprite_url", art)
    trade_digest.sprite_url(
        {"pokemon_id": 25, "form_id": 0, "costume_id": 1, "category": "normal"}
    )
    assert art.call_args.kwargs.get("costume") == 1


def test_announcer_keeps_exact_costume_in_notices():
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from modules.trade_announcer import TradeAnnouncer

    cursor, db = MagicMock(), MagicMock()
    db.cursor.return_value.__enter__.return_value = cursor
    row = {
        "wanter_id": 2,
        "holder_id": 1,
        "pokemon_id": 25,
        "form_id": 0,
        "costume_id": 2,
        "category": "normal",
    }
    TradeAnnouncer._record_notices(SimpleNamespace(_connect=lambda: db), [row])
    query, params = cursor.execute.call_args.args
    assert "costume_id" in query
    assert params == (2, 1, 25, 0, 2, "normal")


@pytest.mark.parametrize(
    "wanted_form,wanted_costume,offered_form,offered_costume,matched",
    [
        (0, 0, 0, 1, True),
        (0, 0, 2676, 0, True),
        (0, 0, 0, 0, True),
        (0, 1, 0, 1, True),
        (0, 2, 0, 1, False),
        (0, 1, 2676, 1, False),
        (2676, 0, 0, 1, False),
    ],
)
@pytest.mark.parametrize("consumer", ["digest", "announcer"])
def test_legacy_wildcards_and_exact_costumes_agree_with_the_site(
    wanted_form, wanted_costume, offered_form, offered_costume, matched, consumer
):
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from modules import trade_digest, trade_announcer

    db = _match_db()
    db.create_function("GREATEST", 2, max)
    db.execute("ALTER TABLE trade_player ADD COLUMN username")
    db.execute("UPDATE trade_player SET username=trainer_name")
    db.execute(
        "CREATE TABLE trade_notice(wanter_id,holder_id,pokemon_id,form_id,costume_id,category)"
    )
    db.execute(
        "UPDATE trade_entry SET form_id=?,costume_id=? WHERE discord_id=1",
        (offered_form, offered_costume),
    )
    if not db.execute(
        "SELECT 1 FROM poliswag.pokemon_catalogue WHERE pokemon_id=25 AND form_id=? AND costume_id=?",
        (offered_form, offered_costume),
    ).fetchone():
        db.execute(
            "INSERT INTO poliswag.pokemon_catalogue VALUES(25,?,?, 'Pikachu','Reviewed costume',1)",
            (offered_form, offered_costume),
        )
    db.execute(
        "INSERT INTO trade_entry VALUES(2,25,?,?,'normal','want',NULL,101)",
        (wanted_form, wanted_costume),
    )
    if consumer == "digest":
        rows = db.execute(
            trade_digest._ROWS_SQL.replace("%s", "?"), (0, 1000)
        ).fetchall()
        offer = next(row for row in rows if row["discord_id"] == 1)
        assert bool(offer["matched"]) is matched
    else:
        cursor, connection = MagicMock(), MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        cursor.fetchone.side_effect = [{"cutoff": 1000}, {"last_trade_notice_at": 0}]
        trade_announcer.TradeAnnouncer._read_batch(
            SimpleNamespace(_connect=lambda: connection)
        )
        statement, parameters = cursor.execute.call_args.args
        rows = db.execute(statement.replace("%s", "?"), parameters).fetchall()
        assert bool(rows) is matched
