"""Small aggregate reader for the shared trades tables."""

from modules import scan_areas
from modules.hundo_alerts import collects_hundo, is_active

# Enough of trade_player for hundo_alerts.is_active, plus area and health.
_HUNDO_SQL = """
SELECT collecting, left_at, hundo_dms, hundo_areas, hundo_settings_revision,
       hundo_confirmed_revision, hundo_dm_refused_revision, hundo_confirmed_setting,
       hundo_health_revision, hundo_health_state
FROM trade_player
WHERE hundo_dms = 1
"""
# Health states that mean alerts are not reaching the player right now.
_HUNDO_UNHEALTHY = ("stopped", "error", "unavailable")


class TradeStats:
    """Read trade activity without exposing player identities or Pokémon IDs."""

    def __init__(self, site_db):
        # The bot's handle for the site's schema (poliswag.site_db).
        self.db = site_db

    async def collect(self, since, until):
        return await self.db.unit(self._collect_sync, since, until)

    def _collect_sync(self, db, since, until):
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

    async def collect_hundo(self):
        """Who has 100IV DMs on right now, by the worker's own rule."""
        rows = await self.db.unit(self._collect_hundo_sync)
        return summarize_hundo(rows)

    def _collect_hundo_sync(self, db):
        with db.cursor() as cursor:
            cursor.execute("SET SESSION max_statement_time = 5")
            cursor.execute(_HUNDO_SQL)
            return list(cursor.fetchall())


def _areas(row):
    # A SET column arrives as "leiria,marinha" and an empty selection means
    # every active area (hundo_alerts._area_keys reads it the same way).
    chosen = {key for key in (row["hundo_areas"] or "").split(",") if key}
    keys = scan_areas.active_keys()
    return chosen & set(keys) if chosen else set(keys)


def summarize_hundo(rows):
    active = [r for r in rows if is_active(r)]
    waiting = [r for r in rows if collects_hundo(r) and not is_active(r)]
    keys = scan_areas.active_keys()
    return {
        "active": len(active),
        "areas": {key: sum(key in _areas(r) for r in active) for key in keys},
        # Switched on but not confirmed yet, or the confirmation DM bounced.
        "waiting": sum(
            r["hundo_dm_refused_revision"] < r["hundo_settings_revision"]
            for r in waiting
        ),
        "dms_closed": sum(
            r["hundo_dm_refused_revision"] >= r["hundo_settings_revision"] > 0
            for r in waiting
        ),
        "unhealthy": sum(
            r["hundo_health_revision"] == r["hundo_settings_revision"]
            and r["hundo_health_state"] in _HUNDO_UNHEALTHY
            for r in active
        ),
    }
