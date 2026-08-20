class CachedBoolSetting:
    """A boolean flag persisted as a single column in the `poliswag` table.

    Cached in memory after the first successful read and updated on every
    write, so it never goes stale but avoids a DB round trip on every
    scheduler tick. A read failure fails open to `default` without caching,
    so a transient DB error is retried on the next read.
    """

    def __init__(self, poliswag, column: str, *, default: bool = True):
        self.poliswag = poliswag
        self._column = column
        self._default = default
        self._cached: bool | None = None

    async def get(self) -> bool:
        if self._cached is not None:
            return self._cached
        try:
            rows = await self.poliswag.db.get_data_from_database(
                f"SELECT {self._column} FROM poliswag LIMIT 1"
            )
            self._cached = bool(rows[0][self._column]) if rows else self._default
            return self._cached
        except Exception as e:
            default_label = "enabled" if self._default else "disabled"
            self.poliswag.utility.log_to_file(
                f"Failed to read {self._column}, defaulting to {default_label}: {e}",
                "ERROR",
            )
            return self._default

    async def set(self, value: bool) -> None:
        try:
            await self.poliswag.db.execute_query_to_database(
                f"UPDATE poliswag SET {self._column} = %s",
                params=(1 if value else 0,),
            )
            self._cached = value
        except Exception as e:
            self.poliswag.utility.log_to_file(
                f"Failed to persist {self._column}: {e}", "ERROR"
            )
