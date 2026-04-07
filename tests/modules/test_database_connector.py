"""Tests for modules.database_connector.DatabaseConnector.

We bypass __init__ (which opens a real connection) by setting instance
attributes directly on a stub object, then exercise execute_query against
a mocked cursor/connection.
"""

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
    return dc, conn


class TestExecuteQueryFetch:
    def test_returns_list_of_dicts(self):
        cursor = _FakeCursor(
            description=[("id",), ("name",)],
            fetch_rows=[(1, "a"), (2, "b")],
        )
        dc, conn = _make_db(cursor)
        result = dc.get_data_from_database("SELECT id, name FROM t")
        assert result == [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}]
        conn.commit.assert_called_once()

    def test_null_description_returns_empty_list(self):
        cursor = _FakeCursor(description=None, fetch_rows=[])
        dc, conn = _make_db(cursor)
        assert dc.get_data_from_database("CALL something()") == []
        conn.commit.assert_called_once()

    def test_passes_params_to_cursor(self):
        cursor = _FakeCursor(description=[("id",)], fetch_rows=[(1,)])
        dc, _ = _make_db(cursor)
        dc.get_data_from_database("SELECT id FROM t WHERE x=%s", params=(42,))
        assert cursor.executed[0] == ("SELECT id FROM t WHERE x=%s", (42,))


class TestExecuteQueryNoFetch:
    def test_returns_rowcount(self):
        cursor = _FakeCursor(rowcount=3)
        dc, conn = _make_db(cursor)
        assert dc.execute_query_to_database("UPDATE t SET x=1") == 3
        conn.commit.assert_called_once()

    def test_zero_rowcount(self):
        cursor = _FakeCursor(rowcount=0)
        dc, _ = _make_db(cursor)
        assert dc.execute_query_to_database("UPDATE t SET x=1") == 0


class TestExecuteQueryErrors:
    def test_non_connection_mysql_error_reraises_immediately(self):
        cursor = _FakeCursor(execute_side_effect=pymysql.MySQLError("Syntax error"))
        dc, _ = _make_db(cursor)
        with pytest.raises(pymysql.MySQLError, match="Syntax"):
            dc.get_data_from_database("SELECT 1")
        # Only the first attempt runs because Syntax errors do not trigger
        # the reconnect/retry branch.
        assert len(cursor.executed) == 1

    def test_unexpected_exception_reraises(self):
        cursor = _FakeCursor(execute_side_effect=RuntimeError("boom"))
        dc, _ = _make_db(cursor)
        with pytest.raises(RuntimeError, match="boom"):
            dc.get_data_from_database("SELECT 1")

    def test_gone_away_triggers_reconnect_then_exhaustion(self, mocker):
        cursor = _FakeCursor(
            execute_side_effect=pymysql.MySQLError("MySQL server has gone away")
        )
        dc, _ = _make_db(cursor)
        # Patch reconnect so it returns a fresh conn whose cursor still throws.
        reconnect = mocker.patch.object(dc, "connect_to_db", return_value=dc.db)
        with pytest.raises(RuntimeError, match="Exceeded maximum retry"):
            dc.get_data_from_database("SELECT 1", retries=2)
        # Both attempts executed; reconnect called for each failure.
        assert len(cursor.executed) == 2
        assert reconnect.call_count == 2

    def test_reconnect_failure_is_swallowed_then_retry_continues(self, mocker):
        cursor = _FakeCursor(
            execute_side_effect=pymysql.MySQLError("Lost connection to server")
        )
        dc, _ = _make_db(cursor)
        reconnect = mocker.patch.object(
            dc, "connect_to_db", side_effect=RuntimeError("cannot reconnect")
        )
        with pytest.raises(RuntimeError, match="Exceeded maximum retry"):
            dc.get_data_from_database("SELECT 1", retries=2)
        assert reconnect.call_count == 2
