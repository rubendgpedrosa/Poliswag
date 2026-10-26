"""Tests for modules.polisnatch_store: the SQL, with a fake connection.

No database: the autouse suite guard already mocks pymysql.connect, and these
tests inject a recording connection so the statements and parameters can be
asserted exactly.
"""

import pytest

from modules.polisnatch_store import (
    NOTIFIED_SQL,
    OPEN_SQL,
    RECORD_SQL,
    SAVE_SQL,
    UNNOTIFIED_SQL,
    JobStore,
)


class FakeCursor:
    def __init__(self, rows=(), rowcount=1):
        self.rows = list(rows)
        self.rowcount = rowcount
        self.calls = []
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.closed = True
        return False

    def execute(self, sql, params=()):
        self.calls.append((sql, params))

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


class FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor
        self.closed = False

    def cursor(self):
        return self._cursor

    def close(self):
        self.closed = True


@pytest.fixture
def cursor():
    return FakeCursor()


@pytest.fixture
def store(cursor):
    return JobStore(connection=FakeConnection(cursor))


class TestRecord:
    def test_inserts_the_job_and_reports_new_rows(self, store, cursor):
        assert (
            store.record(
                "job_0123456789abcdef",
                "lure",
                discord_id=42,
                channel_id=9,
                message_id=555,
                stop_key="geo:nearest",
                lat=39.74,
                lon=-8.80,
                nearest=True,
            )
            is True
        )
        sql, params = cursor.calls[0]
        assert sql == RECORD_SQL
        assert "ON DUPLICATE KEY UPDATE job_id = job_id" in sql
        assert params == (
            "job_0123456789abcdef",
            "lure",
            "queued",
            42,
            9,
            555,
            "geo:nearest",
            39.74,
            -8.80,
            1,
        )

    def test_duplicate_keeps_the_existing_row(self, store, cursor):
        cursor.rowcount = 0
        assert store.record("job_1", "lure") is False
        assert cursor.calls[0][1][1:] == (
            "lure",
            "queued",
            None,
            None,
            None,
            None,
            None,
            None,
            None,
        )

    def test_unknown_nearest_stays_unknown(self, store, cursor):
        store.record("job_1", "link")
        assert cursor.calls[0][1][-1] is None


class TestSave:
    def test_writes_the_poll_fields(self, store, cursor):
        store.save(
            "job_1",
            {
                "jobId": "job_1",
                "state": "done",
                "playerId": "player",
                "trainerName": "Rita99",
                "failReason": None,
                "verified": False,
                "fortId": "fort-1",
            },
        )
        sql, params = cursor.calls[0]
        assert sql == SAVE_SQL
        assert params == ("done", "player", "Rita99", 0, "fort-1", None, "job_1")

    def test_absent_fields_stay_absent_for_coalesce(self, store, cursor):
        store.save("job_1", {"state": "claimed"})
        assert cursor.calls[0][1] == ("claimed", None, None, None, None, None, "job_1")

    def test_missing_state_falls_back_to_queued(self, store, cursor):
        store.save("job_1", {})
        assert cursor.calls[0][1][0] == "queued"


class TestReads:
    def test_open_jobs_are_queued_or_claimed(self, store, cursor):
        cursor.rows = [{"job_id": "job_1", "kind": "lure"}]
        assert store.open_jobs() == [{"job_id": "job_1", "kind": "lure"}]
        sql = cursor.calls[0][0]
        assert sql == OPEN_SQL
        assert "'queued', 'claimed'" in sql

    def test_unnotified_is_terminal_with_a_channel(self, store, cursor):
        cursor.rows = [{"job_id": "job_1"}]
        assert store.unnotified() == [{"job_id": "job_1"}]
        sql = cursor.calls[0][0]
        assert sql == UNNOTIFIED_SQL
        assert "notified_at IS NULL" in sql
        assert "channel_id IS NOT NULL" in sql
        assert "'done', 'failed', 'cancelled'" in sql

    def test_mark_notified(self, store, cursor):
        store.mark_notified("job_1")
        assert cursor.calls[0] == (NOTIFIED_SQL, ("job_1",))


class TestConnections:
    def test_a_given_connection_is_never_closed(self, cursor):
        connection = FakeConnection(cursor)
        JobStore(connection=connection).open_jobs()
        assert connection.closed is False

    def test_without_a_connection_each_call_opens_and_closes_one(self, mocker, cursor):
        connection = FakeConnection(cursor)
        connect = mocker.patch(
            "modules.polisnatch_store.connect", return_value=connection
        )
        JobStore().open_jobs()
        connect.assert_called_once_with(mocker.ANY, dict_rows=True, autocommit=True)
        assert connection.closed is True
