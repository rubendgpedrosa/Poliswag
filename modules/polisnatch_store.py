"""The `polisnatch_job` ledger: what Poliswag submitted and what became of it.

PoliSnatch owns the queue; this table is only Poliswag's memory of it. The bot
opens a short-lived connection per call (same pattern as `modules/lure_locks.py`),
because callers run inside commands and the scheduler tick where every wait is
bounded. The host shiny linker already holds a connection, and on the host
`Config.DB_HOST` is the container name, so it passes its connection in and the
store uses that one without closing it.
"""

from contextlib import closing, contextmanager

from modules.config import Config
from modules.database_connector import connect

RECORD_SQL = """
    INSERT INTO polisnatch_job
        (job_id, kind, state, discord_id, channel_id, message_id, stop_key,
         lat, lon, nearest)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON DUPLICATE KEY UPDATE job_id = job_id
"""
SAVE_SQL = """
    UPDATE polisnatch_job SET
        state = %s,
        player_id = COALESCE(%s, player_id),
        trainer_name = COALESCE(%s, trainer_name),
        verified = COALESCE(%s, verified),
        fort_id = COALESCE(%s, fort_id),
        fail_reason = COALESCE(%s, fail_reason),
        updated_at = UTC_TIMESTAMP()
    WHERE job_id = %s
"""
OPEN_SQL = """
    SELECT * FROM polisnatch_job
    WHERE state IN ('queued', 'claimed')
    ORDER BY submitted_at, job_id
"""
UNNOTIFIED_SQL = """
    SELECT * FROM polisnatch_job
    WHERE notified_at IS NULL
      AND channel_id IS NOT NULL
      AND state IN ('done', 'failed', 'cancelled')
    ORDER BY updated_at, job_id
"""
NOTIFIED_SQL = (
    "UPDATE polisnatch_job SET notified_at = UTC_TIMESTAMP() WHERE job_id = %s"
)


class JobStore:
    def __init__(self, connection=None):
        self._connection = connection

    @contextmanager
    def _cursor(self):
        if self._connection is not None:
            with self._connection.cursor() as cursor:
                yield cursor
            return
        with closing(
            connect(Config.DB_POLISWAG, dict_rows=True, autocommit=True)
        ) as db:
            with db.cursor() as cursor:
                yield cursor

    def record(
        self,
        job_id,
        kind,
        *,
        discord_id=None,
        channel_id=None,
        message_id=None,
        stop_key=None,
        lat=None,
        lon=None,
        nearest=None,
        state="queued",
    ):
        """Adds a job; returns False when it was already there (duplicate)."""
        with self._cursor() as cursor:
            cursor.execute(
                RECORD_SQL,
                (
                    job_id,
                    kind,
                    state or "queued",
                    discord_id,
                    channel_id,
                    message_id,
                    stop_key,
                    lat,
                    lon,
                    None if nearest is None else int(bool(nearest)),
                ),
            )
            return cursor.rowcount == 1

    def save(self, job_id, body):
        """Writes a poll's own fields; absent ones keep their previous value."""
        body = body if isinstance(body, dict) else {}
        verified = body.get("verified")
        with self._cursor() as cursor:
            cursor.execute(
                SAVE_SQL,
                (
                    body.get("state") or "queued",
                    body.get("playerId"),
                    body.get("trainerName"),
                    None if verified is None else int(bool(verified)),
                    body.get("fortId"),
                    body.get("failReason"),
                    job_id,
                ),
            )
            return cursor.rowcount

    def open_jobs(self):
        with self._cursor() as cursor:
            cursor.execute(OPEN_SQL)
            return list(cursor.fetchall())

    def unnotified(self):
        with self._cursor() as cursor:
            cursor.execute(UNNOTIFIED_SQL)
            return list(cursor.fetchall())

    def mark_notified(self, job_id):
        with self._cursor() as cursor:
            cursor.execute(NOTIFIED_SQL, (job_id,))
