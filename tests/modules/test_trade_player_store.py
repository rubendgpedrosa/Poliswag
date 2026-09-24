from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from modules.trade_player_store import TradePlayerStore


@pytest.fixture
def store():
    with patch("modules.trade_player_store.DatabaseConnector") as connector:
        connector.return_value = MagicMock(
            get_data_from_database=AsyncMock(return_value=[]),
            execute_query_to_database=AsyncMock(return_value=1),
        )
        yield TradePlayerStore()


def sql_of(mock):
    return " ".join(mock.call_args[0][0].split())


def params_of(mock):
    return mock.call_args[1]["params"]


async def test_connects_to_the_pogoleiria_schema():
    from modules.config import Config

    with patch("modules.trade_player_store.DatabaseConnector") as connector:
        TradePlayerStore()
    connector.assert_called_once_with(Config.DB_POGOLEIRIA)


async def test_upsert_writes_identity_code_and_clears_left_at(store):
    await store.upsert(
        discord_id=123,
        username="jmboyz",
        display_name="JMBoyz",
        avatar_url="https://cdn/a.png",
        code_hash="a" * 64,
    )
    sql = sql_of(store.db.execute_query_to_database)
    assert "INSERT INTO trade_player" in sql
    assert "ON DUPLICATE KEY UPDATE" in sql
    assert "left_at = NULL" in sql
    assert params_of(store.db.execute_query_to_database)[:5] == (
        123,
        "jmboyz",
        "JMBoyz",
        "https://cdn/a.png",
        "a" * 64,
    )


async def test_refresh_identity_updates_only_existing_rows(store):
    await store.refresh_identity(123, "jmboyz", "JMBoyz", None)
    sql = sql_of(store.db.execute_query_to_database)
    assert sql.startswith("UPDATE trade_player SET")
    assert "code_hash" not in sql
    assert params_of(store.db.execute_query_to_database) == (
        "jmboyz",
        "JMBoyz",
        None,
        123,
    )


async def test_set_left_marks_and_clear_left_unmarks(store):
    await store.set_left(123)
    assert "left_at = UTC_TIMESTAMP()" in sql_of(store.db.execute_query_to_database)

    await store.clear_left(123)
    assert "left_at = NULL" in sql_of(store.db.execute_query_to_database)


async def test_reconcile_marks_leavers_and_restores_returners(store):
    store.db.get_data_from_database.return_value = [
        {"discord_id": 1, "left_at": None},
        {"discord_id": 2, "left_at": None},
        {"discord_id": 3, "left_at": "2026-01-01 00:00:00"},
    ]
    left, returned = await store.reconcile({1, 3})
    assert left == [2]
    assert returned == [3]
