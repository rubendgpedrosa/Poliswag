"""Small aggregate reader for the shared trades tables."""

import asyncio

from modules.config import Config
from modules.database_connector import connect


class TradeStats:
    """Read trade activity without exposing player identities or Pokémon IDs."""

    async def collect(self, since, until):
        return await asyncio.to_thread(self._collect_sync, since, until)

    def _collect_sync(self, since, until):
        db = connect(Config.DB_POGOLEIRIA, dict_rows=True)
        try:
            with db.cursor() as cursor:
                cursor.execute("SET SESSION max_statement_time = 5")
                cursor.execute(
                    """
                    SELECT COUNT(*) entries,
                      COUNT(DISTINCT discord_id) users,
                      SUM(list = 'have') have_entries,
                      SUM(list = 'want') want_entries,
                      COUNT(DISTINCT CASE WHEN list = 'have' THEN discord_id END) have_users,
                      COUNT(DISTINCT CASE WHEN list = 'want' THEN discord_id END) want_users
                    FROM trade_entry
                    WHERE created_at >= %s AND created_at < %s
                    """,
                    (since, until),
                )
                row = cursor.fetchone() or {}
                return {
                    "entries": int(row.get("entries") or 0),
                    "users": int(row.get("users") or 0),
                    "have_entries": int(row.get("have_entries") or 0),
                    "want_entries": int(row.get("want_entries") or 0),
                    "have_users": int(row.get("have_users") or 0),
                    "want_users": int(row.get("want_users") or 0),
                }
        finally:
            db.close()
