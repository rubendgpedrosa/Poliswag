"""Stop locks and a short cooldown for lures.

The bridge keeps only a 60-second dedupe: whether a stop should be lured *again*
is policy, and policy belongs here.

Two ideas, answering different questions:

* **cooldown** — "I just asked for this" — seconds, cleared as soon as the outcome
  is known. It exists to swallow an accidental double press.
* **lock** — "this stop already has a lure" — the lure's lifetime. Written only
  when the worker *verifies* the lure on the map, because a stop the game accepted
  and the map does not show is not worth blocking for half an hour.

The map stays the truth: a worker answers already_lured without spending anything.
This table exists so the bot can say so before queueing a job.
"""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone

from modules.config import Config
from modules.database_connector import connect

LURE_MINUTES = 30
COOLDOWN_SECONDS = 45

CHECK_SQL = "SELECT locked_until, updated_at FROM lure_lock WHERE stop_key = %s"
UPSERT_SQL = """
    INSERT INTO lure_lock (stop_key, fort_id, lure_type, discord_id, locked_until, updated_at)
    VALUES (%s, %s, %s, %s, %s, UTC_TIMESTAMP())
    ON DUPLICATE KEY UPDATE
        fort_id = VALUES(fort_id), lure_type = VALUES(lure_type),
        discord_id = VALUES(discord_id), locked_until = VALUES(locked_until),
        updated_at = UTC_TIMESTAMP()
"""
CLEAR_SQL = "DELETE FROM lure_lock WHERE stop_key = %s"


def _db():
    return closing(connect(Config.DB_POLISWAG, dict_rows=True, autocommit=True))


def stop_key(fort_id=None, lat=None, lon=None) -> str:
    """The same key the bridge uses: a fort id, or coordinates to two decimals."""
    if fort_id:
        return "fort:%s" % fort_id
    return "geo:%.2f,%.2f" % (float(lat), float(lon))


def status(key: str) -> dict:
    """{'locked_for_s': n, 'cooldown_for_s': n}; zeroes mean the stop is free."""
    now = datetime.now(timezone.utc)
    with _db() as db:
        with db.cursor() as cursor:
            cursor.execute(CHECK_SQL, (key,))
            row = cursor.fetchone()
    if not row:
        return {"locked_for_s": 0, "cooldown_for_s": 0}
    locked_until, updated_at = row["locked_until"], row["updated_at"]
    locked = 0
    if locked_until:
        locked = max(
            0, int((locked_until.replace(tzinfo=timezone.utc) - now).total_seconds())
        )
    cooling = 0
    if updated_at:
        cooling = max(
            0,
            COOLDOWN_SECONDS
            - int((now - updated_at.replace(tzinfo=timezone.utc)).total_seconds()),
        )
    return {"locked_for_s": locked, "cooldown_for_s": cooling}


def touch(key: str, fort_id=None, lure_type=None, discord_id=None) -> None:
    """Records the attempt: starts the short cooldown, does not lock the stop."""
    with _db() as db:
        with db.cursor() as cursor:
            cursor.execute(UPSERT_SQL, (key, fort_id, lure_type, discord_id, None))


def lock(
    key: str, fort_id=None, lure_type=None, discord_id=None, minutes: int = LURE_MINUTES
) -> None:
    """The lure is on the map: keep the stop for its lifetime."""
    until = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    with _db() as db:
        with db.cursor() as cursor:
            cursor.execute(UPSERT_SQL, (key, fort_id, lure_type, discord_id, until))


def clear(key: str) -> None:
    with _db() as db:
        with db.cursor() as cursor:
            cursor.execute(CLEAR_SQL, (key,))
