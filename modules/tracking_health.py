"""Notices when the site stops reporting, without waiting to be asked.

The statistics report is something you have to remember to open, which makes
it the wrong place for the one fact that needs to reach you unprompted: that
nothing is being recorded any more. A broken collector and a quiet week look
identical on the page, and the page only lies to whoever opens it.

The check is deliberately dumb. It reads the newest event's timestamp and
compares it to now. It does not try to tell an outage apart from genuine
silence, because it cannot, and it says so in the message rather than
guessing.
"""

import asyncio
from datetime import datetime, timedelta, timezone

import pymysql

from modules.config import Config

# The site is quiet overnight, so hours of silence is normal. A full day with
# nothing at all is not, whatever the hour.
SILENT_AFTER = timedelta(hours=24)
# How long before the same outage is worth mentioning again. Long enough not
# to nag, short enough that it doesn't scroll away unnoticed.
REPEAT_AFTER = timedelta(hours=12)


class TrackingHealth:
    """Reads the collector's last receipt. Never writes to the analytics DB."""

    async def last_event_at(self):
        return await asyncio.to_thread(self._last_event_sync)

    def _last_event_sync(self):
        db = pymysql.connect(
            host=Config.DB_HOST,
            port=Config.DB_PORT,
            user=Config.DB_USER,
            password=Config.DB_PASSWORD,
            database=Config.DB_POGOLEIRIA,
            connect_timeout=5,
            read_timeout=5,
            write_timeout=5,
        )
        try:
            with db.cursor() as cursor:
                cursor.execute("SET SESSION max_statement_time = 5")
                # Page views only: /pogoleiria.apk writes its download counts
                # to the same table, and a download must not make a dead
                # page-view beacon look alive. (idx_event_created covers it.)
                cursor.execute(
                    "SELECT MAX(created_at) FROM page_view"
                    " WHERE event_name = 'tool_view'"
                )
                row = cursor.fetchone()
                return row[0] if row else None
        finally:
            db.close()


def decide(last_event_at, alerted_at, now=None):
    """What to do about the collector right now.

    Pure, so the thresholds can be tested without a database or a clock.
    Returns one of "alert", "recovered" or None, plus the silence in hours.

    `last_event_at` of None means the table is empty: that is an outage from
    a cold start, not a healthy pause, and it is reported as one.
    """
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    if last_event_at is None:
        silence = None
        silent = True
    else:
        silence = now - last_event_at
        silent = silence >= SILENT_AFTER

    if silent:
        if alerted_at is None or now - alerted_at >= REPEAT_AFTER:
            return "alert", silence
        return None, silence
    # Only tell someone it came back if they were told it had gone.
    if alerted_at is not None:
        return "recovered", silence
    return None, silence


def message(action, silence):
    if action == "alert":
        how_long = (
            "todo o histórico consultado"
            if silence is None
            else f"{int(silence.total_seconds() // 3600)} horas"
        )
        return (
            f"⚠️ **A recolha de estatísticas está calada há {how_long}.**\n"
            "Pode ser o coletor, o site, ou mesmo ninguém a visitar — não dá "
            "para distinguir daqui. Vale a pena ver os logs `[track]` e se o "
            "landing está de pé."
        )
    hours = int(silence.total_seconds() // 3600) if silence else 0
    return (
        "✅ **A recolha voltou.** O evento mais recente tem "
        f"{hours}h. O período em silêncio fica na mesma: não houve registos, "
        "e isso não prova que não houve visitas."
    )
