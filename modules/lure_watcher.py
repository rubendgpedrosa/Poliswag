import datetime

from modules.logging_mixin import LoggingMixin

# Standard Pokémon GO lure module item IDs, also used verbatim as golbat's
# pokestop.lure_id. Named directly here rather than looked up from the
# masterfile: WatWowMap's masterfile source names these items "Troy Disk"
# (Niantic's internal codename for the Lure Module leaking through), not the
# in-game item name players actually recognize.
_LURE_NAMES = {
    501: "Normal Lure",
    502: "Glacial Lure",
    503: "Mossy Lure",
    504: "Magnetic Lure",
    505: "Rainy Lure",
}

# Matches scanner_status.py's _MARINHA_LON_MAX: pokestops at or west of this
# longitude are in Marinha Grande, everything east of it is Leiria.
_MARINHA_LON_MAX = -8.9


class LureWatcher(LoggingMixin):
    """Detects newly-placed lure modules on scanned pokestops.

    golbat's pokestop table only records that a lure is active (lure_id,
    lure_expire_timestamp) -- there is no record of which trainer placed it,
    so a notification can say where and what kind, never who.
    """

    def __init__(self, poliswag):
        self.poliswag = poliswag
        # pokestop id -> last-seen lure_expire_timestamp. A value higher than
        # what is stored (and still in the future) means a fresh lure was
        # just placed, not the same one still ticking down.
        self._known_lure_expiry: dict[str, int] = {}
        # First check after startup only seeds state -- it must never
        # announce every lure that was already active before the bot came up.
        self._seeded = False

    def _lure_name(self, lure_id):
        return _LURE_NAMES.get(lure_id, "Lure")

    def _area(self, lon):
        return "Marinha Grande" if lon <= _MARINHA_LON_MAX else "Leiria"

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

    async def check_new_lures(self):
        """Return newly-placed lures as a list of dicts with name, lat, lon,
        area, lure_name, and expires_at (datetime). Empty on the seeding run."""
        try:
            rows = await self.poliswag.quest_search.db.get_data_from_database(
                "SELECT id, name, lat, lon, lure_id, lure_expire_timestamp "
                "FROM pokestop WHERE lure_expire_timestamp > UNIX_TIMESTAMP()"
            )
        except Exception as e:
            self._log(f"Error checking for new lures: {e}")
            return []

        current = {row["id"]: row["lure_expire_timestamp"] for row in rows}
        new_lures = []

        if self._seeded:
            for row in rows:
                prev_expiry = self._known_lure_expiry.get(row["id"])
                if prev_expiry is None or row["lure_expire_timestamp"] > prev_expiry:
                    new_lures.append(
                        {
                            "name": row["name"] or "PokéStop",
                            "lat": row["lat"],
                            "lon": row["lon"],
                            "area": self._area(row["lon"]),
                            "lure_name": self._lure_name(row["lure_id"]),
                            "expires_at": datetime.datetime.fromtimestamp(
                                row["lure_expire_timestamp"]
                            ),
                        }
                    )
        else:
            self._seeded = True

        self._known_lure_expiry = current
        return new_lures
