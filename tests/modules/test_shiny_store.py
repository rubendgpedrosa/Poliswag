from unittest.mock import AsyncMock, MagicMock

import pytest

from modules.shiny_store import ShinyStore


@pytest.fixture
def db():
    d = MagicMock()
    d.get_data_from_database = AsyncMock(return_value=[])
    d.execute_query_to_database = AsyncMock(return_value=1)
    d.execute_transaction = AsyncMock()
    return d


@pytest.fixture
def store(db):
    return ShinyStore(db)


async def test_get_is_none_for_a_stranger(store):
    assert await store.get(7) is None


async def test_get_returns_the_row(store, db):
    db.get_data_from_database.return_value = [{"status": "linked"}]
    assert await store.get(7) == {"status": "linked"}
    assert db.get_data_from_database.call_args.kwargs["params"] == (7,)


async def test_request_queues_the_code_and_resets_an_old_result(store, db):
    await store.request(7, "rita", "123456789012")
    sql = db.execute_query_to_database.call_args.args[0]
    assert db.execute_query_to_database.call_args.kwargs["params"] == (
        7,
        "rita",
        "123456789012",
    )
    assert "ON DUPLICATE KEY UPDATE" in sql
    for reset in (
        "status = 'pending'",
        "player_id = NULL",
        "trainer_name = NULL",
        "attempts = 0",
        "announced_at = NULL",
    ):
        assert reset in sql


async def test_request_truncates_a_long_name(store, db):
    await store.request(7, "x" * 80, "123456789012")
    assert len(db.execute_query_to_database.call_args.kwargs["params"][1]) == 64


async def test_remove_deletes_alerts_and_link_together(store, db):
    await store.remove(7)
    statements = db.execute_transaction.call_args.args[0]
    assert [s[0].split()[2] for s in statements] == ["shiny_alert", "shiny_link"]
    assert all(s[1] == (7,) for s in statements)


async def test_recipients_are_linked_players_with_a_channel_or_on_the_list(store, db):
    db.get_data_from_database.return_value = [{"discord_id": 7, "player_id": "a" * 16}]
    assert await store.recipients([7, 8]) == [{"discord_id": 7, "player_id": "a" * 16}]
    sql = db.get_data_from_database.call_args.args[0]
    assert "status = 'linked'" in sql
    assert "(channel_id IS NOT NULL OR discord_id IN (%s,%s))" in sql
    assert "alert_leiria" in sql and "alert_marinha" in sql
    assert "discord_name" in sql  # Poracle wants a name for its target
    assert db.get_data_from_database.call_args.kwargs["params"] == (7, 8)


async def test_with_nobody_listed_players_with_a_channel_still_are(store, db):
    await store.recipients([])
    sql = db.get_data_from_database.call_args.args[0]
    assert "status = 'linked' AND channel_id IS NOT NULL" in sql
    assert db.get_data_from_database.call_args.kwargs["params"] == ()


async def test_watermark_is_none_before_the_first_tick(store, db):
    db.get_data_from_database.return_value = [{"shiny_alert_watermark": None}]
    assert await store.watermark() is None


async def test_record_alert_is_true_only_the_first_time(store, db):
    db.execute_query_to_database.return_value = 1
    assert await store.record_alert(7, "123", 25, 0) is True
    db.execute_query_to_database.return_value = 0
    assert await store.record_alert(7, "123", 25, 0) is False
    assert "INSERT IGNORE" in db.execute_query_to_database.call_args.args[0]


async def test_a_verdict_is_stored_once(store, db):
    db.execute_query_to_database.return_value = 1
    assert await store.set_verdict(7, "123", "shiny") is True
    assert "verdict IS NULL" in db.execute_query_to_database.call_args.args[0]
    db.execute_query_to_database.return_value = 0
    assert await store.set_verdict(7, "123", "not_shiny") is False


async def test_a_linked_player_is_found_by_discord_or_trainer_name(store, db):
    db.get_data_from_database.return_value = [
        {"discord_id": 7, "trainer_name": "Rita99"}
    ]
    assert await store.find_linked("rita99") == {
        "discord_id": 7,
        "trainer_name": "Rita99",
    }
    sql = db.get_data_from_database.call_args.args[0]
    assert (
        "status = 'linked'" in sql and "discord_name" in sql and "trainer_name" in sql
    )
    assert db.get_data_from_database.call_args.kwargs["params"] == ("rita99", "rita99")


async def test_nobody_by_that_name_is_none(store):
    assert await store.find_linked("ninguém") is None


async def test_the_map_token_is_stored_as_given(store, db):
    await store.set_map_token(7, "ab" * 32)
    assert "map_token_hash" in db.execute_query_to_database.call_args.args[0]
    assert db.execute_query_to_database.call_args.kwargs["params"] == ("ab" * 32, 7)


async def test_the_players_channel_is_stored(store, db):
    await store.set_channel(7, 999)
    assert "channel_id" in db.execute_query_to_database.call_args.args[0]
    assert db.execute_query_to_database.call_args.kwargs["params"] == (999, 7)


async def test_find_linked_also_returns_the_channel(store, db):
    await store.find_linked("rita99")
    assert "channel_id" in db.get_data_from_database.call_args.args[0]


async def test_never_shiny_is_the_marked_species_and_forms(store, db):
    db.get_data_from_database.return_value = [
        {"pokemon_id": 931, "form_id": 2987},
        {"pokemon_id": 835, "form_id": 3172},
    ]
    assert await store.never_shiny() == {(931, 2987), (835, 3172)}
    assert "never_shiny = 1" in db.get_data_from_database.call_args.args[0]
