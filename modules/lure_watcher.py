import datetime

# Standard Pokémon GO lure module item IDs, also used verbatim as golbat's
# pokestop.lure_id. Fallback only -- _lure_name() prefers the live masterfile
# name when it is loaded, same as quest_search's item/pokemon reward text.
_FALLBACK_LURE_NAMES = {
    501: "Lure Normal",
    502: "Lure Glacial",
    503: "Lure Almiscarado",
    504: "Lure Magnético",
    505: "Lure Chuvoso",
}


class LureWatcher:
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

    def _log(self, msg, level="ERROR"):
        self.poliswag.utility.log_to_file(msg, level)

    def _lure_name(self, lure_id):
        masterfile = self.poliswag.quest_search.masterfile_data
        if masterfile and "items" in masterfile:
            item_data = masterfile["items"].get(str(lure_id))
            if isinstance(item_data, dict) and "name" in item_data:
                return item_data["name"]
            if isinstance(item_data, str):
                return item_data
        return _FALLBACK_LURE_NAMES.get(lure_id, "Lure")

    async def check_new_lures(self):
        """Return newly-placed lures as a list of dicts with name, lat, lon,
        lure_name, and expires_at (datetime). Empty on the seeding run."""
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
