import asyncio
import time

import pymysql
import logging
from modules.config import Config

# pymysql error codes that indicate a lost/broken connection that may recover
# on reconnect. 2006 = "MySQL server has gone away", 2013 = "Lost connection".
_RECONNECT_ERRNOS = {2006, 2013}
# 2003 = "Can't connect to MySQL server". ping(reconnect=True) re-raises its
# internal reconnect failure (typically 2003) instead of the original 2006 /
# 2013, so pre-execution recovery also retries 2003. Post-execution failures
# are never replayed regardless of errno (see _execute_query_sync).
_PREEXEC_RETRY_ERRNOS = _RECONNECT_ERRNOS | {2003}


def connect(database, *, read_timeout=5, dict_rows=False, **extra):
    """Short-lived connection for a one-off read or write; caller closes it.
    Every wait is bounded because callers run inside the 60s scheduler tick,
    where an unbounded read stalls every later step.
    """
    if dict_rows:
        extra["cursorclass"] = pymysql.cursors.DictCursor
    return pymysql.connect(
        host=Config.DB_HOST,
        port=Config.DB_PORT,
        user=Config.DB_USER,
        password=Config.DB_PASSWORD,
        database=database,
        connect_timeout=3,
        read_timeout=read_timeout,
        write_timeout=3,
        **extra,
    )


class DatabaseConnector:
    def __init__(self, database=None):
        self.database = database or Config.DB_POLISWAG
        self.db = self.connect_to_db()
        # pymysql connections are not thread-safe for concurrent use, so every
        # call is serialized. The lock is held until the worker thread
        # actually finishes (see _run_serialized): cancelling the awaiting
        # coroutine releases nothing early, the next caller blocks until the
        # outstanding thread is done, and cancellation still propagates.
        self._lock = asyncio.Lock()

    def connect_to_db(self):
        try:
            db = pymysql.connect(
                host=Config.DB_HOST,
                port=Config.DB_PORT,
                user=Config.DB_USER,
                password=Config.DB_PASSWORD,
                db=self.database,
                # Bound every wait: an unbounded read hangs the caller, and
                # callers here run inside the 60s scheduler tick. Loose enough
                # for the reporting queries this connector also serves. A read
                # timeout arrives as errno 2013: before execution starts it is
                # recovered with a bounded reconnect-and-retry, but once
                # execution has started it is never replayed -- the failure
                # rolls back, refreshes the connection once for the next
                # caller, and raises the original error.
                connect_timeout=5,
                read_timeout=30,
                write_timeout=30,
            )
            logging.info("Successfully connected to the database.")
            return db
        except pymysql.MySQLError as e:
            logging.error(f"Failed to connect to the database: {e}")
            raise

    async def get_data_from_database(self, query, retries=3, params=None):
        return await self._run_serialized(
            self._execute_query_sync, query, True, retries, params
        )

    async def execute_query_to_database(self, query, retries=3, params=None):
        return await self._run_serialized(
            self._execute_query_sync, query, False, retries, params
        )

    async def execute_transaction(self, statements):
        """Apply a small related write set atomically on the shared connection.

        Do not replay on connection loss: commit outcome could be uncertain.
        The next scheduler tick reconciles from stored state instead.
        """
        return await self._run_serialized(self._transaction_sync, statements)

    async def _run_serialized(self, func, *args):
        """Run func(*args) in a worker thread while holding the connection.

        The asyncio.Lock is acquired in the caller and released only after
        the worker thread finishes -- never when the waiter is cancelled.
        asyncio.shield prevents a cancelled waiter from cancelling the
        worker Task itself (a cancelled to_thread Task reports done() while
        its thread still runs, which would release early). On cancellation
        the waiter gets CancelledError promptly, but the lock stays held
        until the thread completes, so no second caller can reuse the shared
        pymysql connection while work is outstanding. The worker
        result/exception is consumed on completion so no unretrieved-exception
        warnings escape.
        """
        await self._lock.acquire()
        worker = asyncio.ensure_future(asyncio.to_thread(func, *args))
        try:
            result = await asyncio.shield(worker)
        except asyncio.CancelledError:
            if worker.done():
                try:
                    self._lock.release()
                except RuntimeError:
                    pass
                if not worker.cancelled():
                    try:
                        worker.result()
                    except BaseException:
                        pass
            else:

                def _release(fut):
                    try:
                        self._lock.release()
                    except RuntimeError:
                        pass
                    try:
                        fut.result()
                    except BaseException:
                        pass

                worker.add_done_callback(_release)
            raise
        except BaseException:
            try:
                self._lock.release()
            except RuntimeError:
                pass
            raise
        else:
            self._lock.release()
            return result

    def _close_broken_connection(self):
        """Close the current suspect connection after a breakage."""
        try:
            self.db.close()
        except Exception as e:
            logging.error(f"Error closing broken connection: {e}")

    def _refresh_after_breakage(self):
        """Invalidate the broken connection for the next caller.

        Closes the broken handle and makes one bounded attempt to establish
        a fresh connection for whoever runs next. Never replays the failed
        statement itself; the caller still raises its original error. A
        failed refresh only logs -- the next call will try again.
        """
        self._close_broken_connection()
        try:
            self.db = self.connect_to_db()
        except Exception as reconnect_err:
            logging.error(f"Reconnect failed: {reconnect_err}")

    def _rollback_quietly(self, context):
        """Best-effort rollback that never masks the caller's error.

        Returns the rollback exception (or None) so the caller can decide
        whether the connection also needs invalidating. Every one-off
        operation rolls back on failure so an open failed-transaction state
        never bleeds into the next call.
        """
        try:
            self.db.rollback()
        except Exception as rb_exc:
            logging.error(f"Rollback failed during {context}: {rb_exc!r}")
            return rb_exc
        return None

    def _execute_query_sync(self, query, fetch, retries, params=None):
        # Conservative retry contract (no SQL classification): bounded
        # recovery is allowed ONLY before cursor.execute begins -- a ping or
        # cursor-creation failure means nothing reached the server, so
        # reconnect-and-retry is safe. Once execution starts, there is NO
        # replay on execute/fetch/commit failure for EITHER API, even for
        # get_data_from_database: an arbitrary CALL/WITH can write, and a
        # 2006/2013 after execute or during commit leaves the outcome
        # uncertain (replaying an increment would double-apply it). The
        # failure rolls back, refreshes the connection once for the next
        # caller, and raises the original error; the next scheduler tick
        # retries domain reads naturally. `retries` therefore bounds only
        # pre-execution recovery attempts.
        last_error = None
        for attempt in range(retries):
            try:
                self.db.ping(reconnect=True)
            except pymysql.MySQLError as ping_err:
                last_error = ping_err
                ping_errno = ping_err.args[0] if ping_err.args else None
                logging.error(
                    f"Database ping failed on attempt {attempt + 1}: {ping_err}"
                )
                if ping_errno in _PREEXEC_RETRY_ERRNOS:
                    self._refresh_after_breakage()
                    time.sleep(0.25 * (attempt + 1))
                    continue
                rb_exc = self._rollback_quietly("ping")
                if rb_exc is not None:
                    self._refresh_after_breakage()
                raise
            except Exception as ping_unexp:
                logging.error(f"Unexpected ping error: {ping_unexp}")
                rb_exc = self._rollback_quietly("ping")
                if rb_exc is not None:
                    self._refresh_after_breakage()
                raise
            started = False
            try:
                with self.db.cursor() as cursor:
                    started = True
                    cursor.execute(query, params)
                    if fetch:
                        raw_rows = cursor.fetchall()
                        description = cursor.description
                    else:
                        affected_rows = cursor.rowcount
                self.db.commit()
                if fetch:
                    if description is None:
                        return []
                    columns = [col[0] for col in description]
                    return [
                        {columns[i]: row[i] for i in range(len(columns))}
                        for row in raw_rows
                    ]
                return affected_rows
            except pymysql.MySQLError as e:
                last_error = e
                errno = e.args[0] if e.args else None
                logging.error(f"Database error on attempt {attempt + 1}: {e}")
                if not started and errno in _PREEXEC_RETRY_ERRNOS:
                    # Cursor creation failed before execute: nothing was sent,
                    # so reconnect and retry is safe (bounded by retries).
                    self._rollback_quietly("cursor setup")
                    try:
                        self._close_broken_connection()
                        self.db = self.connect_to_db()
                    except Exception as reconnect_err:
                        logging.error(f"Reconnect failed: {reconnect_err}")
                    time.sleep(0.25 * (attempt + 1))
                    continue
                # Execution started (or non-connection error): clean up and
                # raise the original without replay. Any rollback failure
                # discards the connection: the transaction state is unknown
                # and the handle must not be reused by the next call.
                rb_exc = self._rollback_quietly("query")
                if errno in _RECONNECT_ERRNOS or rb_exc is not None:
                    self._refresh_after_breakage()
                if rb_exc is not None:
                    raise e from None
                raise
            except Exception as e:
                logging.error(f"Unexpected error: {e}")
                rb_exc = self._rollback_quietly("query")
                if rb_exc is not None:
                    self._refresh_after_breakage()
                    raise e from None
                raise
        raise RuntimeError(f"Exceeded maximum retry attempts: {last_error}")

    def _transaction_sync(self, statements):
        try:
            self.db.ping(reconnect=True)
        except pymysql.MySQLError as e:
            logging.error(f"Transaction ping failed: {e}")
            self._refresh_after_breakage()
            raise
        try:
            self.db.begin()
        except pymysql.MySQLError as e:
            logging.error(f"Transaction begin failed: {e}")
            errno = e.args[0] if e.args else None
            if errno in _PREEXEC_RETRY_ERRNOS:
                self._refresh_after_breakage()
            raise
        try:
            with self.db.cursor() as cursor:
                for sql, params in statements:
                    cursor.execute(sql, params)
            self.db.commit()
        except BaseException as exc:
            orig_errno = None
            if isinstance(exc, pymysql.MySQLError) and exc.args:
                orig_errno = exc.args[0]
            try:
                self.db.rollback()
            except Exception as rb_exc:
                logging.error(f"Rollback failed after {exc!r}: {rb_exc!r}")
                # Any rollback failure discards the handle: the transaction
                # state is unknown and must not be reused by the next call.
                self._refresh_after_breakage()
                raise exc from None
            if orig_errno in _RECONNECT_ERRNOS:
                self._refresh_after_breakage()
            raise
