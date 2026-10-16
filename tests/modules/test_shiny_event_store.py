from unittest.mock import AsyncMock, MagicMock

import pytest

from modules.shiny_store import ShinyStore


@pytest.fixture
def store():
    db = MagicMock(
        get_data_from_database=AsyncMock(return_value=[]),
        execute_query_to_database=AsyncMock(return_value=1),
    )
    return ShinyStore(db)


async def test_cooldown_claim_is_atomic_and_bound_to_opted_in_linked_user(store):
    assert await store.claim_event_digest(111)
    sql = store.db.execute_query_to_database.call_args.args[0]
    assert (
        "event_alerts_enabled = 1" in sql
        and "INTERVAL 5 MINUTE" in sql
        and "status = 'linked'" in sql
    )
    store.db.execute_query_to_database.return_value = 0
    assert not await store.claim_event_digest(111)


async def test_no_pilot_recipients_means_no_query(store):
    assert await store.event_recipients([]) == []
    store.db.get_data_from_database.assert_not_awaited()


async def test_same_preferences_report_success_without_changed_row(store):
    store.db.execute_query_to_database.return_value = 0
    store.db.get_data_from_database.return_value = [
        {"event_alerts_enabled": 1, "event_pokemon_ids": "[25, 570]"}
    ]
    assert await store.set_event_preferences(111, True, [25, 570])
    store.db.get_data_from_database.return_value = []
    assert not await store.set_event_preferences(111, True, [25, 570])


async def test_event_records_preserve_prediction_evidence(store):
    spawn = {"id": "9007199254740993", "pokemon_id": 570, "form_id": 2108}
    assert await store.record_event_alert(111, spawn, 25, 0.01)
    params = store.db.execute_query_to_database.call_args.kwargs["params"]
    assert params == (111, "9007199254740993", 570, 2108, 25, 0.01)


async def test_delivery_is_updated_only_for_own_pending_rows(store):
    await store.mark_event_delivery(111, ["one", "two"], False)
    sql = store.db.execute_query_to_database.call_args.args[0]
    assert "delivery_status = 'pending'" in sql
    assert store.db.execute_query_to_database.call_args.kwargs["params"] == (
        "failed",
        "failed",
        111,
        "one",
        "two",
    )
