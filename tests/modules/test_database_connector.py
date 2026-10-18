"""Tests for modules.database_connector.DatabaseConnector.

We bypass __init__ (which opens a real connection) by setting instance
attributes directly on a stub object, then exercise the async wrappers
against a mocked cursor/connection.
"""

import asyncio
import threading
from unittest.mock import MagicMock

import pymysql
import pytest

from modules.database_connector import DatabaseConnector


class TestConnectToDb:
    def test_success_returns_connection_and_logs(self, mocker):
        conn = MagicMock()
        connect = mocker.patch(
            "modules.database_connector.pymysql.connect", return_value=conn
        )
        info = mocker.patch("modules.database_connector.logging.info")
        dc = DatabaseConnector.__new__(DatabaseConnector)
        dc.database = "poliswag"
        assert dc.connect_to_db() is conn
        connect.assert_called_once()
        info.assert_called_once()

    def test_failure_logs_and_reraises(self, mocker):
        mocker.patch(
            "modules.database_connector.pymysql.connect",
            side_effect=pymysql.MySQLError("connection refused"),
        )
        error = mocker.patch("modules.database_connector.logging.error")
        dc = DatabaseConnector.__new__(DatabaseConnector)
        dc.database = "poliswag"
        with pytest.raises(pymysql.MySQLError, match="connection refused"):
            dc.connect_to_db()
        error.assert_called_once()


class TestInit:
    def test_sets_up_database_connection_and_lock(self, mocker):
        conn = MagicMock()
        mocker.patch.object(DatabaseConnector, "connect_to_db", return_value=conn)
        dc = DatabaseConnector(database="custom_db")
        assert dc.database == "custom_db"
        assert dc.db is conn
        assert isinstance(dc._lock, asyncio.Lock)

    def test_defaults_database_from_config(self, mocker):
        conn = MagicMock()
        mocker.patch.object(DatabaseConnector, "connect_to_db", return_value=conn)
        mocker.patch("modules.database_connector.Config.DB_POLISWAG", "poliswag_db")
        dc = DatabaseConnector()
        assert dc.database == "poliswag_db"


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
    async def test_non_connection_mysql_error_reraises_and_rolls_back(self):
        cursor = _FakeCursor(execute_side_effect=pymysql.MySQLError("Syntax error"))
        dc, conn = _make_db(cursor)
        with pytest.raises(pymysql.MySQLError, match="Syntax"):
            await dc.get_data_from_database("SELECT 1")
        # Single execution attempt, then rollback so no open failed
        # transaction bleeds into the next call.
        assert len(cursor.executed) == 1
        conn.rollback.assert_called_once()

    async def test_unexpected_exception_reraises_and_rolls_back(self):
        cursor = _FakeCursor(execute_side_effect=RuntimeError("boom"))
        dc, conn = _make_db(cursor)
        with pytest.raises(RuntimeError, match="boom"):
            await dc.get_data_from_database("SELECT 1")
        assert len(cursor.executed) == 1
        conn.rollback.assert_called_once()

    async def test_ping_loss_exhausts_bounded_preexecution_retries(self, mocker):
        # Ping fails before execute on every attempt: nothing reached the
        # server, so bounded reconnect/retry is safe, then exhaustion raises.
        cursor = _FakeCursor(description=[("id",)], fetch_rows=[(1,)])
        dc, conn = _make_db(cursor)
        conn.ping.side_effect = pymysql.MySQLError(2006, "gone away on ping")
        reconnect = mocker.patch.object(dc, "_refresh_after_breakage")
        mocker.patch("modules.database_connector.time.sleep")
        with pytest.raises(RuntimeError, match="Exceeded maximum retry"):
            await dc.get_data_from_database("SELECT 1", retries=2)
        assert len(cursor.executed) == 0
        assert reconnect.call_count == 2

    async def test_ping_reconnect_failure_is_swallowed_then_retry_continues(
        self, mocker
    ):
        cursor = _FakeCursor(description=[("id",)], fetch_rows=[(1,)])
        dc, conn = _make_db(cursor)
        conn.ping.side_effect = pymysql.MySQLError(2013, "lost on ping")
        # _refresh_after_breakage swallows its own reconnect failure; the
        # loop still continues until retries are exhausted.
        mocker.patch.object(
            dc,
            "connect_to_db",
            side_effect=pymysql.MySQLError(2003, "cannot reconnect"),
        )
        mocker.patch("modules.database_connector.time.sleep")
        with pytest.raises(RuntimeError, match="Exceeded maximum retry"):
            await dc.get_data_from_database("SELECT 1", retries=2)
        assert len(cursor.executed) == 0

    async def test_ping_2003_recovers_before_execution(self, mocker):
        # ping(reconnect=True) re-raises its internal reconnect failure as
        # 2003 ("can't connect") instead of the original 2006/2013. That is
        # still a pre-execution failure -- nothing reached the server -- so
        # bounded reconnect-and-retry applies and the retry uses a new
        # handle.
        bad_conn = MagicMock()
        bad_conn.ping.side_effect = pymysql.MySQLError(2003, "can't connect")
        good_cursor = _FakeCursor(description=[("id",)], fetch_rows=[(7,)])
        good_conn = MagicMock()
        good_conn.cursor.return_value = good_cursor
        dc = DatabaseConnector.__new__(DatabaseConnector)
        dc.database = "poliswag"
        dc._lock = asyncio.Lock()
        dc.db = bad_conn
        mocker.patch.object(dc, "connect_to_db", return_value=good_conn)
        mocker.patch("modules.database_connector.time.sleep")
        result = await dc.get_data_from_database("SELECT 1", retries=2)
        assert result == [{"id": 7}]
        assert len(good_cursor.executed) == 1
        bad_conn.close.assert_called_once()
        assert dc.db is good_conn


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


class TestConnectionTimeouts:
    """A connection with no timeouts can hang a caller indefinitely.

    Everything here runs through asyncio.to_thread from the 60s scheduler
    tick, so an unbounded read stalls that tick and every step after it.
    """

    def test_connect_sets_timeouts(self):
        import unittest.mock as mock

        with mock.patch("pymysql.connect") as connect:
            DatabaseConnector(database="poliswag")
        kwargs = connect.call_args.kwargs
        assert kwargs["connect_timeout"]
        assert kwargs["read_timeout"]
        assert kwargs["write_timeout"]


class TestShortLivedConnect:
    def test_bounded_waits_and_passthrough(self):
        import unittest.mock as mock

        import pymysql

        from modules.database_connector import connect

        with mock.patch("pymysql.connect") as pm:
            connect("pogoleiria", read_timeout=10, dict_rows=True, autocommit=False)
        kwargs = pm.call_args.kwargs
        assert kwargs["database"] == "pogoleiria"
        assert kwargs["connect_timeout"] and kwargs["write_timeout"]
        assert kwargs["read_timeout"] == 10
        assert kwargs["cursorclass"] is pymysql.cursors.DictCursor
        assert kwargs["autocommit"] is False

    def test_tuple_rows_by_default(self):
        import unittest.mock as mock

        from modules.database_connector import connect

        with mock.patch("pymysql.connect") as pm:
            connect("pogoleiria")
        assert "cursorclass" not in pm.call_args.kwargs


class TestCancellationKeepsSerialization:
    """Cancellation must not release the shared connection early.

    A threading.Event blocks the worker thread while the test cancels the
    waiter; the lock must stay held until the thread actually finishes, the
    waiter must still see CancelledError, and the next caller must wait.
    """

    async def test_cancellation_holds_lock_until_worker_finishes(self):
        cursor = _FakeCursor(description=[("id",)], fetch_rows=[(1,)])
        dc, _ = _make_db(cursor)
        entered = threading.Event()
        release = threading.Event()

        def blocking_sync(query, fetch, retries, params=None):
            entered.set()
            assert release.wait(timeout=5), "worker was never released"
            return dc._execute_query_sync(query, fetch, retries, params)

        task = asyncio.create_task(
            dc._run_serialized(blocking_sync, "SELECT 1", True, 3, None)
        )
        task2 = None
        try:
            loop = asyncio.get_running_loop()
            assert await loop.run_in_executor(
                None, entered.wait, 5
            ), "worker never started"
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            # Worker thread is still blocked: the lock must still be held so
            # no second caller can reuse the connection while work is
            # outstanding.
            assert dc._lock.locked()
            second_entered = threading.Event()

            def second_sync():
                second_entered.set()
                return "second"

            task2 = asyncio.create_task(dc._run_serialized(second_sync))
            await asyncio.sleep(0.1)
            assert not second_entered.is_set()
            release.set()
            assert await asyncio.wait_for(asyncio.shield(task2), timeout=5) == (
                "second"
            )
            assert await task2 == "second"
            assert not dc._lock.locked()
        finally:
            # A failed assertion above must not leave the worker thread
            # blocked forever.
            release.set()

    async def test_repeated_cancellation_still_propagates_and_serializes(self):
        cursor = _FakeCursor(description=[("id",)], fetch_rows=[(1,)])
        dc, _ = _make_db(cursor)
        entered = threading.Event()
        release = threading.Event()

        def blocking_sync(query, fetch, retries, params=None):
            entered.set()
            assert release.wait(timeout=5)
            return "late-result"

        task = asyncio.create_task(
            dc._run_serialized(blocking_sync, "SELECT 1", True, 3, None)
        )
        try:
            loop = asyncio.get_running_loop()
            assert await loop.run_in_executor(None, entered.wait, 5)
            task.cancel()
            # Repeated cancellation while the worker is still outstanding.
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert dc._lock.locked()
            release.set()
            # Give the worker thread a moment to finish and release the lock.
            for _ in range(100):
                if not dc._lock.locked():
                    break
                await asyncio.sleep(0.02)
            assert not dc._lock.locked()
        finally:
            # A failed assertion above must not leave the worker thread
            # blocked forever.
            release.set()

    async def test_no_connection_reuse_while_work_is_outstanding(self):
        cursor = _FakeCursor(description=[("id",)], fetch_rows=[(1,)])
        dc, _ = _make_db(cursor)
        first_entered = threading.Event()
        first_release = threading.Event()
        active = 0
        max_active = 0
        counter_lock = threading.Lock()

        def first_sync(query, fetch, retries, params=None):
            nonlocal active, max_active
            with counter_lock:
                active += 1
                max_active = max(max_active, active)
            first_entered.set()
            assert first_release.wait(timeout=5)
            with counter_lock:
                active -= 1
            return []

        def second_sync(query, fetch, retries, params=None):
            nonlocal active, max_active
            with counter_lock:
                active += 1
                max_active = max(max_active, active)
            with counter_lock:
                active -= 1
            return []

        loop = asyncio.get_running_loop()
        task1 = asyncio.create_task(
            dc._run_serialized(first_sync, "SELECT 1", True, 3, None)
        )
        try:
            assert await loop.run_in_executor(None, first_entered.wait, 5)
            task2 = asyncio.create_task(
                dc._run_serialized(second_sync, "SELECT 2", True, 3, None)
            )
            await asyncio.sleep(0.1)
            assert not task2.done()
            assert max_active == 1
            first_release.set()
            await asyncio.wait_for(asyncio.gather(task1, task2), timeout=5)
            assert max_active == 1
            assert not dc._lock.locked()
        finally:
            # A failed assertion above must not leave the worker thread
            # blocked forever.
            first_release.set()


class TestConservativeRetry:
    """Bounded recovery ONLY before execute; no replay once started.

    No SQL classification: an arbitrary CALL/WITH can write, so both APIs
    share one conservative contract. Healthy reads and writes recover from
    a dead connection before execution, but a failed execute/commit is
    called exactly once and raises its original error.
    """

    async def test_read_recovers_from_dead_connection_before_execution(self, mocker):
        good_cursor = _FakeCursor(description=[("id",)], fetch_rows=[(7,)])
        dc = DatabaseConnector.__new__(DatabaseConnector)
        dc.database = "poliswag"
        dc._lock = asyncio.Lock()
        bad_conn = MagicMock()
        bad_conn.ping.side_effect = pymysql.MySQLError(2006, "gone away on ping")
        good_conn = MagicMock()
        good_conn.cursor.return_value = good_cursor
        dc.db = bad_conn
        mocker.patch.object(
            dc,
            "_refresh_after_breakage",
            side_effect=lambda: setattr(dc, "db", good_conn),
        )
        mocker.patch("modules.database_connector.time.sleep")
        result = await dc.get_data_from_database("SELECT 1", retries=2)
        assert result == [{"id": 7}]
        assert len(good_cursor.executed) == 1

    async def test_write_recovers_from_dead_connection_before_execution(self, mocker):
        good_cursor = _FakeCursor(rowcount=2)
        dc = DatabaseConnector.__new__(DatabaseConnector)
        dc.database = "poliswag"
        dc._lock = asyncio.Lock()
        bad_conn = MagicMock()
        bad_conn.ping.side_effect = pymysql.MySQLError(2006, "gone away on ping")
        good_conn = MagicMock()
        good_conn.cursor.return_value = good_cursor
        dc.db = bad_conn
        mocker.patch.object(
            dc,
            "_refresh_after_breakage",
            side_effect=lambda: setattr(dc, "db", good_conn),
        )
        mocker.patch("modules.database_connector.time.sleep")
        result = await dc.execute_query_to_database("UPDATE t SET x=1", retries=2)
        assert result == 2
        assert len(good_cursor.executed) == 1

    async def test_write_execute_loss_called_once_no_replay(self, mocker):
        cursor = _FakeCursor(
            execute_side_effect=pymysql.MySQLError(2006, "MySQL server has gone away")
        )
        dc, conn = _make_db(cursor)
        refresh = mocker.patch.object(dc, "_refresh_after_breakage")
        with pytest.raises(pymysql.MySQLError):
            await dc.execute_query_to_database("UPDATE t SET x = x + 1")
        # Increment-style write executed once and was NOT replayed.
        assert len(cursor.executed) == 1
        assert conn.commit.call_count == 0
        conn.rollback.assert_called_once()
        assert refresh.call_count == 1

    async def test_read_execute_loss_called_once_no_replay(self, mocker):
        # Even through get_data_from_database: a failed execute may have
        # been a CALL that wrote, so no automatic replay.
        cursor = _FakeCursor(
            execute_side_effect=pymysql.MySQLError(2006, "gone away on execute")
        )
        dc, conn = _make_db(cursor)
        refresh = mocker.patch.object(dc, "_refresh_after_breakage")
        with pytest.raises(pymysql.MySQLError, match="gone away on execute"):
            await dc.get_data_from_database("CALL maybe_writes()", retries=3)
        assert len(cursor.executed) == 1
        conn.rollback.assert_called_once()
        assert refresh.call_count == 1

    async def test_write_commit_failure_called_once_no_retry(self, mocker):
        cursor = _FakeCursor(rowcount=1)
        dc, conn = _make_db(cursor)
        conn.commit.side_effect = pymysql.MySQLError(2006, "gone away during commit")
        refresh = mocker.patch.object(dc, "_refresh_after_breakage")
        with pytest.raises(pymysql.MySQLError, match="gone away during commit"):
            await dc.execute_query_to_database("UPDATE t SET x = x + 1")
        assert len(cursor.executed) == 1
        assert conn.commit.call_count == 1
        conn.rollback.assert_called_once()
        assert refresh.call_count == 1

    async def test_read_commit_failure_called_once_no_retry(self, mocker):
        cursor = _FakeCursor(description=[("id",)], fetch_rows=[(1,)])
        dc, conn = _make_db(cursor)
        conn.commit.side_effect = pymysql.MySQLError(2013, "lost during commit")
        refresh = mocker.patch.object(dc, "_refresh_after_breakage")
        with pytest.raises(pymysql.MySQLError, match="lost during commit"):
            await dc.get_data_from_database("SELECT 1")
        assert len(cursor.executed) == 1
        assert conn.commit.call_count == 1
        conn.rollback.assert_called_once()
        assert refresh.call_count == 1


class TestRollbackFailureInvalidatesConnection:
    """ANY rollback failure discards the handle, not just 2006/2013.

    A failed rollback leaves the transaction state unknown, so the next
    call must use a new handle while the caller still sees its original
    error.
    """

    async def test_mysql_error_with_failed_rollback_discards_handle(self, mocker):
        bad_cursor = _FakeCursor(execute_side_effect=pymysql.MySQLError("stmt failed"))
        dc = DatabaseConnector.__new__(DatabaseConnector)
        dc.database = "poliswag"
        dc._lock = asyncio.Lock()
        old_conn = MagicMock()
        old_conn.cursor.return_value = bad_cursor
        old_conn.rollback.side_effect = RuntimeError("rollback exploded")
        dc.db = old_conn
        good_cursor = _FakeCursor(description=[("id",)], fetch_rows=[(9,)])
        new_conn = MagicMock()
        new_conn.cursor.return_value = good_cursor
        mocker.patch.object(dc, "connect_to_db", return_value=new_conn)
        with pytest.raises(pymysql.MySQLError, match="stmt failed"):
            await dc.get_data_from_database("SELECT 1")
        old_conn.rollback.assert_called_once()
        old_conn.close.assert_called_once()
        assert dc.db is new_conn
        result = await dc.get_data_from_database("SELECT 1")
        assert result == [{"id": 9}]
        assert len(bad_cursor.executed) == 1
        assert len(good_cursor.executed) == 1

    async def test_unexpected_error_with_failed_rollback_discards_handle(self, mocker):
        bad_cursor = _FakeCursor(execute_side_effect=RuntimeError("boom"))
        dc = DatabaseConnector.__new__(DatabaseConnector)
        dc.database = "poliswag"
        dc._lock = asyncio.Lock()
        old_conn = MagicMock()
        old_conn.cursor.return_value = bad_cursor
        old_conn.rollback.side_effect = RuntimeError("rollback exploded")
        dc.db = old_conn
        good_cursor = _FakeCursor(description=[("id",)], fetch_rows=[(9,)])
        new_conn = MagicMock()
        new_conn.cursor.return_value = good_cursor
        mocker.patch.object(dc, "connect_to_db", return_value=new_conn)
        with pytest.raises(RuntimeError, match="boom"):
            await dc.get_data_from_database("SELECT 1")
        old_conn.rollback.assert_called_once()
        old_conn.close.assert_called_once()
        assert dc.db is new_conn
        result = await dc.get_data_from_database("SELECT 1")
        assert result == [{"id": 9}]
        assert len(bad_cursor.executed) == 1
        assert len(good_cursor.executed) == 1


class TestTransactions:
    def _make_tx_db(self, cursor, conn=None):
        conn = conn or MagicMock()
        conn.cursor.return_value = cursor
        dc = DatabaseConnector.__new__(DatabaseConnector)
        dc.database = "poliswag"
        dc.db = conn
        dc._lock = asyncio.Lock()
        return dc, conn

    async def test_transaction_commits_once_on_success(self):
        cursor = _FakeCursor()
        dc, conn = self._make_tx_db(cursor)
        await dc.execute_transaction(
            [("UPDATE t SET x=1", None), ("UPDATE t SET y=2", None)]
        )
        assert len(cursor.executed) == 2
        conn.commit.assert_called_once()
        conn.rollback.assert_not_called()

    async def test_transaction_rollback_on_statement_failure(self):
        class FailingCursor(_FakeCursor):
            def execute(self, query, params):
                self.executed.append((query, params))
                if query == "BAD":
                    raise ValueError("stmt failed")

        cursor = FailingCursor()
        dc, conn = self._make_tx_db(cursor)
        with pytest.raises(ValueError, match="stmt failed"):
            await dc.execute_transaction([("OK", None), ("BAD", None)])
        conn.rollback.assert_called_once()
        conn.commit.assert_not_called()

    async def test_transaction_rollback_failure_preserves_original(self, mocker):
        cursor = _FakeCursor(execute_side_effect=ValueError("stmt failed"))
        dc, conn = self._make_tx_db(cursor)
        conn.rollback.side_effect = RuntimeError("rollback failed")
        new_conn = MagicMock()
        new_conn.cursor.return_value = _FakeCursor()
        mocker.patch.object(dc, "connect_to_db", return_value=new_conn)
        with pytest.raises(ValueError, match="stmt failed"):
            await dc.execute_transaction([("UPDATE t SET x=1", None)])
        conn.rollback.assert_called_once()
        conn.commit.assert_not_called()
        # The failed rollback discards the suspect handle even though
        # neither error carries a 2006/2013 errno.
        conn.close.assert_called_once()
        assert dc.db is new_conn

    async def test_transaction_rollback_failure_next_uses_new_handle(self, mocker):
        cursor = _FakeCursor(execute_side_effect=ValueError("stmt failed"))
        dc, old_conn = self._make_tx_db(cursor)
        old_conn.rollback.side_effect = RuntimeError("rollback failed")
        new_cursor = _FakeCursor()
        new_conn = MagicMock()
        new_conn.cursor.return_value = new_cursor
        mocker.patch.object(dc, "connect_to_db", return_value=new_conn)
        with pytest.raises(ValueError, match="stmt failed"):
            await dc.execute_transaction([("UPDATE t SET x=1", None)])
        assert dc.db is new_conn
        await dc.execute_transaction([("UPDATE t SET y=2", None)])
        assert len(new_cursor.executed) == 1
        new_conn.commit.assert_called_once()
        new_conn.rollback.assert_not_called()

    async def test_transaction_failed_commit_called_once(self, mocker):
        cursor = _FakeCursor()
        dc, conn = self._make_tx_db(cursor)
        conn.commit.side_effect = pymysql.MySQLError(2006, "gone away on commit")
        refresh = mocker.patch.object(dc, "_refresh_after_breakage")
        with pytest.raises(pymysql.MySQLError, match="gone away on commit"):
            await dc.execute_transaction([("UPDATE t SET x=1", None)])
        assert conn.commit.call_count == 1
        conn.rollback.assert_called_once()
        assert refresh.call_count == 1

    async def test_transaction_statement_loss_invalidates_connection(self, mocker):
        cursor = _FakeCursor(
            execute_side_effect=pymysql.MySQLError(2013, "lost during statement")
        )
        dc, conn = self._make_tx_db(cursor)
        refresh = mocker.patch.object(dc, "_refresh_after_breakage")
        with pytest.raises(pymysql.MySQLError, match="lost during statement"):
            await dc.execute_transaction([("UPDATE t SET x=1", None)])
        conn.rollback.assert_called_once()
        assert refresh.call_count == 1
