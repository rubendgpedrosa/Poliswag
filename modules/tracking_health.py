"""Record website traffic silence as information, never as a collector outage.

An absent page view cannot distinguish a quiet site from a broken collector.
Real service health is monitored independently.
"""

import asyncio
from datetime import datetime, timedelta, timezone

from modules.config import Config
from modules.database_connector import connect

# The site is quiet overnight. A full day without recorded page views is
# useful context, but is not evidence of a service failure.
SILENT_AFTER = timedelta(hours=24)


class TrackingHealth:
    """Reads the collector's last receipt. Never writes to the analytics DB."""

    async def last_event_at(self):
        return await asyncio.to_thread(self._last_event_sync)

    def _last_event_sync(self):
        db = connect(Config.DB_POGOLEIRIA)
        try:
            with db.cursor() as cursor:
                cursor.execute("SET SESSION max_statement_time = 5")
                # Page views only: /pogoleiria.apk writes its download counts
                # to the same table; count page-view activity separately.
                # idx_event_created covers this query.
                cursor.execute(
                    "SELECT MAX(created_at) FROM page_view"
                    " WHERE event_name = 'tool_view'"
                )
                row = cursor.fetchone()
                return row[0] if row else None
        finally:
            db.close()


def decide(last_event_at, alerted_at, now=None):
    """Return a once-per-episode traffic observation and the silence duration.

    The existing persisted `alerted_at` column tracks when silence was logged.
    Empty history is also an observation, with no inferred outage or duration.
    """
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    if last_event_at is None:
        silence = None
        silent = True
    else:
        silence = now - last_event_at
        silent = silence >= SILENT_AFTER

    if silent:
        if alerted_at is None:
            return "alert", silence
        return None, silence
    # Log resumption only after recording a silent episode.
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
            f"Sem visualizações de páginas registadas há {how_long}. "
            "A ausência de visitas não permite avaliar a saúde do coletor."
        )
    return "Atividade no site retomada: há visualizações de páginas recentes."
