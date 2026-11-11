import time

# How long a value read from the database is trusted. The switches had
# Discord commands until 2026-11-11 (`!container autorecreate`, `!device
# autoreboot`); now the column itself is the switch, so a change made in SQL
# has to be noticed without a restart.
MAX_AGE_SECONDS = 300


class CachedBoolSetting:
    """A boolean flag kept in one column of the `poliswag` table.

    Read at most once every MAX_AGE_SECONDS, so the scheduler does not pay a
    database round trip on every pass and a change to the column takes effect
    within five minutes:

        UPDATE poliswag SET auto_recreate_enabled = 0;

    A read that fails returns the value last read, or `default` when there
    is none yet, and is tried again on the next call. The column name goes
    into the SQL as text, so pass only a fixed name.
    """

    def __init__(self, poliswag, column: str, *, default: bool = True):
        self.poliswag = poliswag
        self._column = column
        self._default = default
        self._cached: bool | None = None
        self._read_at = 0.0

    async def get(self) -> bool:
        now = time.monotonic()
        if self._cached is not None and now - self._read_at < MAX_AGE_SECONDS:
            return self._cached
        try:
            rows = await self.poliswag.db.get_data_from_database(
                f"SELECT {self._column} FROM poliswag LIMIT 1"
            )
            self._cached = bool(rows[0][self._column]) if rows else self._default
            self._read_at = now
            return self._cached
        except Exception as e:
            if self._cached is not None:
                return self._cached
            default_label = "enabled" if self._default else "disabled"
            self.poliswag.utility.log_to_file(
                f"Failed to read {self._column}, defaulting to {default_label}: {e}",
                "ERROR",
            )
            return self._default
