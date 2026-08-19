"""Tests for modules.database_connector.DatabaseConnector.

We bypass __init__ (which opens a real connection) by setting instance
attributes directly on a stub object, then exercise the async wrappers
against a mocked cursor/connection.
"""

import asyncio
from unittest.mock import MagicMock

import pymysql
import pytest

from modules.database_connector import DatabaseConnector


class _FakeCursor:
    def __init__(
        self,
        *,
        description=None,
        fetch_rows=(),
        rowcount=0,
        execute_side_effect=None,
    ):
        self.description = description
        self._fetch_rows = fetch_rows
        self.rowcount = rowcount
        self._execute_side_effect = execute_side_effect
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, query, params):
        self.executed.append((query, params))
        if self._execute_side_effect is not None:
            raise self._execute_side_effect

    def fetchall(self):
        return self._fetch_rows


def _make_db(cursor):
    conn = MagicMock()
    conn.cursor.return_value = cursor
    dc = DatabaseConnector.__new__(DatabaseConnector)
    dc.database = "poliswag"
    dc.db = conn
    dc._lock = asyncio.Lock()
    return dc, conn


class TestExecuteQueryFetch:
    async def test_returns_list_of_dicts(self):
        cursor = _FakeCursor(
            description=[("id",), ("name",)],
            fetch_rows=[(1, "a"), (2, "b")],
        )
        dc, conn = _make_db(cursor)
        result = await dc.get_data_from_database("SELECT id, name FROM t")
        assert result == [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}]
        conn.commit.assert_called_once()

    async def test_null_description_returns_empty_list(self):
        cursor = _FakeCursor(description=None, fetch_rows=[])
        dc, conn = _make_db(cursor)
        assert await dc.get_data_from_database("CALL something()") == []
        conn.commit.assert_called_once()

    async def test_passes_params_to_cursor(self):
        cursor = _FakeCursor(description=[("id",)], fetch_rows=[(1,)])
        dc, _ = _make_db(cursor)
        await dc.get_data_from_database("SELECT id FROM t WHERE x=%s", params=(42,))
        assert cursor.executed[0] == ("SELECT id FROM t WHERE x=%s", (42,))


class TestExecuteQueryNoFetch:
    async def test_returns_rowcount(self):
        cursor = _FakeCursor(rowcount=3)
        dc, conn = _make_db(cursor)
        assert await dc.execute_query_to_database("UPDATE t SET x=1") == 3
        conn.commit.assert_called_once()

    async def test_zero_rowcount(self):
        cursor = _FakeCursor(rowcount=0)
        dc, _ = _make_db(cursor)
        assert await dc.execute_query_to_database("UPDATE t SET x=1") == 0


class TestExecuteQueryErrors:
    async def test_non_connection_mysql_error_reraises_immediately(self):
        cursor = _FakeCursor(execute_side_effect=pymysql.MySQLError("Syntax error"))
        dc, _ = _make_db(cursor)
        with pytest.raises(pymysql.MySQLError, match="Syntax"):
            await dc.get_data_from_database("SELECT 1")
        # Only the first attempt runs because Syntax errors do not trigger
        # the reconnect/retry branch.
        assert len(cursor.executed) == 1

    async def test_unexpected_exception_reraises(self):
        cursor = _FakeCursor(execute_side_effect=RuntimeError("boom"))
        dc, _ = _make_db(cursor)
        with pytest.raises(RuntimeError, match="boom"):
            await dc.get_data_from_database("SELECT 1")

    async def test_gone_away_triggers_reconnect_then_exhaustion(self, mocker):
        cursor = _FakeCursor(
            execute_side_effect=pymysql.MySQLError(2006, "MySQL server has gone away")
        )
        dc, _ = _make_db(cursor)
        # Patch reconnect so it returns a fresh conn whose cursor still throws.
        reconnect = mocker.patch.object(dc, "connect_to_db", return_value=dc.db)
        # Patch sleep to avoid slow tests.
        mocker.patch("modules.database_connector.time.sleep")
        with pytest.raises(RuntimeError, match="Exceeded maximum retry"):
            await dc.get_data_from_database("SELECT 1", retries=2)
        # Both attempts executed; reconnect called for each failure.
        assert len(cursor.executed) == 2
        assert reconnect.call_count == 2

    async def test_reconnect_failure_is_swallowed_then_retry_continues(self, mocker):
        cursor = _FakeCursor(
            execute_side_effect=pymysql.MySQLError(2013, "Lost connection to server")
        )
        dc, _ = _make_db(cursor)
        reconnect = mocker.patch.object(
            dc,
            "connect_to_db",
            side_effect=pymysql.MySQLError(2003, "cannot reconnect"),
        )
        mocker.patch("modules.database_connector.time.sleep")
        with pytest.raises(RuntimeError, match="Exceeded maximum retry"):
            await dc.get_data_from_database("SELECT 1", retries=2)
        assert reconnect.call_count == 2


class TestAsyncWrappers:
    """The public methods dispatch through to_thread under the shared lock."""

    async def test_get_data_uses_to_thread(self, mocker):
        cursor = _FakeCursor(description=[("id",)], fetch_rows=[(1,)])
        dc, _ = _make_db(cursor)
        to_thread = mocker.patch(
            "modules.database_connector.asyncio.to_thread",
            side_effect=lambda fn, *a: fn(*a),
        )
        await dc.get_data_from_database("SELECT id FROM t")
        to_thread.assert_called_once()
        assert to_thread.call_args.args[0] == dc._execute_query_sync

    async def test_execute_query_uses_to_thread(self, mocker):
        cursor = _FakeCursor(rowcount=1)
        dc, _ = _make_db(cursor)
        to_thread = mocker.patch(
            "modules.database_connector.asyncio.to_thread",
            side_effect=lambda fn, *a: fn(*a),
        )
        await dc.execute_query_to_database("UPDATE t SET x=1")
        to_thread.assert_called_once()
        assert to_thread.call_args.args[0] == dc._execute_query_sync

    async def test_concurrent_calls_are_serialized_by_the_lock(self):
        # Two calls that both need the lock — the second must not start its
        # to_thread dispatch until the first has released it.
        cursor = _FakeCursor(description=[("id",)], fetch_rows=[(1,)])
        dc, _ = _make_db(cursor)
        order = []

        real_to_thread = asyncio.to_thread

        async def tracking_to_thread(fn, *args):
            order.append("start")
            result = await real_to_thread(fn, *args)
            order.append("end")
            return result

        import unittest.mock as mock

        with mock.patch(
            "modules.database_connector.asyncio.to_thread",
            side_effect=tracking_to_thread,
        ):
            await asyncio.gather(
                dc.get_data_from_database("SELECT 1"),
                dc.get_data_from_database("SELECT 1"),
            )
        # Serialized: start/end pairs never interleave.
        assert order == ["start", "end", "start", "end"]
