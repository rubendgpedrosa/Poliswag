from modules.logging_mixin import LoggingMixin


class LureWatcher(LoggingMixin):
    """Counts active lure modules on scanned pokestops, for the bot's
    presence line."""

    def __init__(self, poliswag):
        self.poliswag = poliswag

    async def count_active_lures(self):
        """Current count of pokestops with an active lure right now."""
        try:
            rows = await self.poliswag.quest_search.db.get_data_from_database(
                "SELECT COUNT(*) AS active FROM pokestop "
                "WHERE lure_expire_timestamp > UNIX_TIMESTAMP()"
            )
        except Exception as e:
            self._log(f"Error counting active lures: {e}")
            return 0
        return rows[0]["active"] if rows else 0
