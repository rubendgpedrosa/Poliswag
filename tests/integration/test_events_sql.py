"""Real event SQL. Only runs against the marked disposable server from the runner."""

import os
import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pymysql
import pytest

from cogs.scheduled import Scheduled
from modules.event_manager import EventManager
from modules.database_connector import DatabaseConnector
from modules.utility import Utility

_REAL_CONNECT = pymysql.connect
pytestmark = pytest.mark.skipif(
    not os.getenv("EVENT_SQL_TEST_PORT"), reason="No disposable event database"
)


@pytest.fixture
def store():
    db = _REAL_CONNECT(
        host="127.0.0.1",
        port=int(os.environ["EVENT_SQL_TEST_PORT"]),
        user="root",
        password="",
        autocommit=True,
        cursorclass=pymysql.cursors.DictCursor,
    )
    with db.cursor() as cur:
        cur.execute("SHOW DATABASES")
        schemas = {r["Database"] for r in cur.fetchall()}
        assert "event_disposable" in schemas and not schemas.intersection(
            {"golbat", "dragonite", "poracle"}
        )
        cur.execute("CREATE DATABASE IF NOT EXISTS poliswag")
        cur.execute("USE poliswag")
        cur.execute("DROP TABLE IF EXISTS event")
        cur.execute("DROP TABLE IF EXISTS excluded_event_type")
        cur.execute("""CREATE TABLE event (
            name VARCHAR(255), start DATETIME, end DATETIME, image VARCHAR(255),
            event_type VARCHAR(50), link VARCHAR(255), extra_data JSON,
            notification_date DATETIME, notification_end_date DATETIME,
            PRIMARY KEY(name, start))""")
        cur.execute("CREATE TABLE excluded_event_type (type VARCHAR(50) PRIMARY KEY)")
        cur.execute("SET time_zone = '+00:00'")

    class Store:
        async def get_data_from_database(self, sql, params=None):
            with db.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchall()

        async def execute_query_to_database(self, sql, params=None):
            with db.cursor() as cur:
                return cur.execute(sql, params)

        async def execute_transaction(self, statements):
            connector = DatabaseConnector.__new__(DatabaseConnector)
            connector.db = db
            connector._lock = asyncio.Lock()
            return await connector.execute_transaction(statements)

    yield Store()
    db.close()


def bot_for(store):
    bot = MagicMock()
    bot.db = store
    bot.utility.format_datetime_string = Utility.__new__(Utility).format_datetime_string
    bot.event_manager = EventManager(bot)
    bot.event_stats.get_summary = AsyncMock(return_value=None)
    return bot


def source(**changes):
    return {
        "eventID": "lisbon-safari",
        "name": "Lisbon Safari",
        "eventType": "event",
        "start": "2026-09-26T09:00:00Z",
        "end": "2026-09-27T17:00:00Z",
        **changes,
    }


def sender(bot):
    cog = Scheduled.__new__(Scheduled)
    cog.poliswag = bot
    return cog


async def test_ingest_deliver_restart_and_exact_end(store):
    bot = bot_for(store)
    manager = bot.event_manager
    manager.events = [source()]
    await manager.process_and_store_events()
    await manager.process_and_store_events()
    rows = await store.get_data_from_database("SELECT * FROM event")
    assert len(rows) == 1
    assert rows[0]["start"] == datetime(2026, 9, 26, 10)
    assert rows[0]["end"] == datetime(2026, 9, 27, 18)
    assert (
        await manager.check_current_events_changes(
            at_time=datetime(2026, 9, 26, 9, 59, 59)
        )
        is None
    )
    changed = await manager.check_current_events_changes(
        at_time=datetime(2026, 9, 26, 10)
    )
    assert len(changed["started"]) == 1
    channel = SimpleNamespace(send=AsyncMock(side_effect=RuntimeError("offline")))
    with pytest.raises(RuntimeError):
        await sender(bot)._send_event_change_notifications(
            channel, changed, acknowledge=True
        )
    assert (await store.get_data_from_database("SELECT notification_date FROM event"))[
        0
    ]["notification_date"] is None
    # New manager represents a restart; the undelivered row must still be due.
    bot = bot_for(store)
    changed = await bot.event_manager.check_current_events_changes(
        at_time=datetime(2026, 9, 26, 10)
    )
    channel.send = AsyncMock()
    await sender(bot)._send_event_change_notifications(
        channel, changed, acknowledge=True
    )
    manager = bot_for(store).event_manager
    manager.events = [source()]
    await manager.process_and_store_events()
    assert (
        await manager.check_current_events_changes(at_time=datetime(2026, 9, 26, 10))
        is None
    )
    assert (
        await manager.check_current_events_changes(
            at_time=datetime(2026, 9, 27, 17, 59, 59)
        )
        is None
    )
    changed = await manager.check_current_events_changes(
        at_time=datetime(2026, 9, 27, 18)
    )
    assert len(changed["ended"]) == 1 and not changed["started"]
    await sender(bot)._send_event_change_notifications(
        channel, changed, acknowledge=True
    )
    assert (
        await manager.check_current_events_changes(at_time=datetime(2026, 9, 27, 18))
        is None
    )


async def test_reschedule_and_rename_preserve_notification_state(store):
    bot = bot_for(store)
    manager = bot.event_manager
    manager.events = [source()]
    await manager.process_and_store_events()
    await store.execute_query_to_database(
        "UPDATE event SET notification_date='2026-09-26 10:00:01'"
    )
    manager.events = [
        source(
            name="Lisbon Safari renamed",
            start="2026-09-26T10:00:00Z",
            end="2026-09-28T17:00:00Z",
        )
    ]
    await manager.process_and_store_events()
    await manager.process_and_store_events()
    [row] = await store.get_data_from_database("SELECT * FROM event")
    assert row["name"] == "Lisbon Safari renamed"
    assert row["start"] == datetime(2026, 9, 26, 11)
    assert row["end"] == datetime(2026, 9, 28, 18)
    assert row["notification_date"] == datetime(2026, 9, 26, 10, 0, 1)
    assert row["notification_end_date"] is None


async def test_end_only_change_and_excluded_events(store):
    manager = bot_for(store).event_manager
    manager.events = [source()]
    await manager.process_and_store_events()
    manager.events = [source(end="2026-09-27T18:00:00Z")]
    await manager.process_and_store_events()
    [row] = await store.get_data_from_database("SELECT * FROM event")
    assert row["end"] == datetime(2026, 9, 27, 19)
    await store.execute_query_to_database(
        "INSERT INTO excluded_event_type VALUES ('event')"
    )
    assert (
        await manager.check_current_events_changes(at_time=datetime(2026, 9, 26, 12))
        is None
    )


async def test_partial_batch_retry_after_restart(store):
    bot = bot_for(store)
    bot.event_manager.events = [
        source(eventID=f"e{i}", name=f"Event {i:02}") for i in range(12)
    ]
    await bot.event_manager.process_and_store_events()
    changed = await bot.event_manager.check_current_events_changes(
        at_time=datetime(2026, 9, 26, 10)
    )
    channel = SimpleNamespace(
        send=AsyncMock(side_effect=[None, RuntimeError("offline")])
    )
    with pytest.raises(RuntimeError):
        await sender(bot)._send_event_change_notifications(
            channel, changed, acknowledge=True
        )
    retry = await bot_for(store).event_manager.check_current_events_changes(
        at_time=datetime(2026, 9, 26, 10)
    )
    assert len(retry["started"]) == 2
    assert {e["name"] for e in retry["started"]} == {
        e["name"] for e in changed["started"][10:]
    }


async def test_future_rename_keeps_previously_sent_marker(store):
    manager = bot_for(store).event_manager
    manager.events = [source(start="2099-09-26T09:00:00Z", end="2099-09-27T17:00:00Z")]
    await manager.process_and_store_events()
    await store.execute_query_to_database(
        "UPDATE event SET notification_date='2026-09-26 10:00:01'"
    )
    manager.events = [
        source(
            name="Renamed future event",
            start="2099-09-26T10:00:00Z",
            end="2099-09-27T17:00:00Z",
        )
    ]
    await manager.process_and_store_events()
    [row] = await store.get_data_from_database("SELECT * FROM event")
    assert row["name"] == "Renamed future event"
    assert row["notification_date"] == datetime(2026, 9, 26, 10, 0, 1)


async def test_same_named_distinct_feed_ids_remain_separate(store):
    manager = bot_for(store).event_manager
    manager.events = [
        source(),
        source(eventID="lisbon-second", start="2026-09-26T11:00:00Z"),
    ]
    await manager.process_and_store_events()
    manager.events[1]["start"] = "2026-09-26T12:00:00Z"
    await manager.process_and_store_events()
    rows = await store.get_data_from_database("SELECT * FROM event ORDER BY start")
    assert [r["start"] for r in rows] == [
        datetime(2026, 9, 26, 10),
        datetime(2026, 9, 26, 13),
    ]


async def test_manual_preview_includes_exact_end_minute_without_marking(store):
    manager = bot_for(store).event_manager
    manager.events = [source()]
    await manager.process_and_store_events()
    changed = await manager.check_current_events_changes(
        at_time=datetime(2026, 9, 27, 18), dry_run=True
    )
    assert len(changed["ended"]) == 1
    [row] = await store.get_data_from_database(
        "SELECT notification_date, notification_end_date FROM event"
    )
    assert row == {"notification_date": None, "notification_end_date": None}


async def test_cancelled_same_name_event_removed_without_deleting_survivor(store):
    manager = bot_for(store).event_manager
    a = source(
        eventID="first", start="2099-09-26T09:00:00Z", end="2099-09-27T17:00:00Z"
    )
    b = source(
        eventID="second", start="2099-09-28T09:00:00Z", end="2099-09-29T17:00:00Z"
    )
    manager.events = [a, b]
    await manager.process_and_store_events()
    manager.events = [b]
    await manager.process_and_store_events()
    [row] = await store.get_data_from_database("SELECT * FROM event")
    assert row["start"] == datetime(2099, 9, 28, 10)


async def test_rename_and_cancellation_of_same_name_preserve_delivered_marker(store):
    manager = bot_for(store).event_manager
    a = source(
        eventID="first", start="2099-09-26T09:00:00Z", end="2099-09-27T17:00:00Z"
    )
    b = source(
        eventID="second", start="2099-09-28T09:00:00Z", end="2099-09-29T17:00:00Z"
    )
    manager.events = [a, b]
    await manager.process_and_store_events()
    await store.execute_query_to_database(
        "UPDATE event SET notification_date='2026-09-26 10:00:01'"
    )
    manager.events = [{**b, "name": "New name"}]
    await manager.process_and_store_events()
    [row] = await store.get_data_from_database("SELECT * FROM event")
    assert row["name"] == "New name"
    assert row["notification_date"] == datetime(2026, 9, 26, 10, 0, 1)


async def test_incomplete_placeholder_retains_prior_future_row(store):
    manager = bot_for(store).event_manager
    event = source(start="2099-09-26T09:00:00Z", end="2099-09-27T17:00:00Z")
    manager.events = [event]
    await manager.process_and_store_events()
    manager.events = [{**event, "start": None, "end": None}]
    await manager.process_and_store_events()
    [row] = await store.get_data_from_database("SELECT * FROM event")
    assert row["start"] == datetime(2099, 9, 26, 10)


async def test_obsolete_source_copies_merge_to_current_schedule(store):
    manager = bot_for(store).event_manager
    current = source(start="2099-09-26T10:00:00", end="2099-09-27T20:00:00")
    # Reproduce old ingestion that left both midnight and the revised 10:00.
    for start in ("2099-09-26 00:00:00", "2099-09-26 10:00:00"):
        sql, params = manager.build_upsert_query(
            current["name"], start, "2099-09-27 23:59:00", "", "event", "", current
        )
        await store.execute_query_to_database(sql, params)
    await store.execute_query_to_database(
        "UPDATE event SET notification_date='2026-09-26 09:00:00' WHERE start='2099-09-26 00:00:00'"
    )
    manager.events = [current]
    await manager.process_and_store_events()
    await manager.process_and_store_events()
    [row] = await store.get_data_from_database("SELECT * FROM event")
    assert row["start"] == datetime(2099, 9, 26, 10)
    assert row["end"] == datetime(2099, 9, 27, 20)
    assert row["notification_date"] == datetime(2026, 9, 26, 9)


async def test_source_copy_merge_rolls_back_if_cleanup_fails(store, mocker):
    manager = bot_for(store).event_manager
    current = source(start="2099-09-26T10:00:00", end="2099-09-27T20:00:00")
    for start in ("2099-09-26 00:00:00", "2099-09-26 10:00:00"):
        sql, params = manager.build_upsert_query(
            current["name"], start, "2099-09-27 23:59:00", "", "event", "", current
        )
        await store.execute_query_to_database(sql, params)
    before = await store.get_data_from_database("SELECT * FROM event ORDER BY start")
    real_transaction = store.execute_transaction

    async def broken(statements):
        await real_transaction(
            statements + [("UPDATE table_that_does_not_exist SET missing = 1", ())]
        )

    mocker.patch.object(store, "execute_transaction", side_effect=broken)
    manager.events = [current]
    assert await manager.process_and_store_events() is False
    assert (
        await store.get_data_from_database("SELECT * FROM event ORDER BY start")
        == before
    )
