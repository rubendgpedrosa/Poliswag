import re

from modules.logging_mixin import LoggingMixin

# Golbat's *_stats tables (populated by golbat itself, not written by
# poliswag) are date-granularity, not per-minute -- there's no way to
# isolate exactly the event's hours from the rest of that day. For
# Community Day / Raid Day / Raid Hour (a few boosted hours on one day),
# the boosted activity dominates the day's total, so the daily sum is a
# reasonable proxy; it's not exact for events that share a day with
# unrelated activity.
_COMMUNITY_DAY_SUFFIX = re.compile(r"\s*community day\s*$", re.IGNORECASE)
_STATS_AREAS = ("Leiria", "MarinhaGrande")


class EventStats(LoggingMixin):
    """Post-event stats summaries, sourced from golbat's own aggregate
    stats tables (raid_stats, pokemon_stats, pokemon_hundo_stats)."""

    def __init__(self, poliswag):
        self.poliswag = poliswag

    async def get_summary(self, event: dict) -> str | None:
        """A short PT-PT stats line for a finished event, or None if the
        event type isn't one we have a stats source for (or the species
        for a Community Day couldn't be resolved from its name)."""
        event_type = (event.get("event_type") or "").lower()
        try:
            start_date = str(event["start"])[:10]
            end_date = str(event["end"])[:10]
        except (KeyError, TypeError):
            return None

        try:
            if "raid" in event_type:
                return await self._raid_summary(start_date, end_date)
            if "community" in event_type:
                return await self._community_day_summary(
                    event.get("name", ""), start_date, end_date
                )
        except Exception as e:
            self._log(f"[EVENTSTATS] Failed to build summary: {e}")
            return None
        return None

    async def _raid_summary(self, start_date, end_date) -> str:
        total = await self._sum("raid_stats", None, start_date, end_date)
        return f"🥊 **{total}** raids durante o evento."

    async def _community_day_summary(self, name, start_date, end_date) -> str | None:
        species = _COMMUNITY_DAY_SUFFIX.sub("", name).strip()
        if not species:
            return None
        pokemon_id = self._resolve_pokemon_id(species)
        if pokemon_id is None:
            return None
        spawns = await self._sum("pokemon_stats", pokemon_id, start_date, end_date)
        hundos = await self._sum(
            "pokemon_hundo_stats", pokemon_id, start_date, end_date
        )
        return f"🐾 **{spawns}** spawns · 💯 **{hundos}** 100% IV"

    def _resolve_pokemon_id(self, species: str) -> int | None:
        qs = self.poliswag.quest_search
        matches = qs.get_pokemon_id_by_pokemon_name_map(species)
        exact = [
            mid
            for mid in matches
            if qs.pokemon_name_map.get(mid, "") == species.lower()
        ]
        if exact:
            return int(exact[0])
        if len(matches) == 1:
            return int(matches[0])
        return None

    async def _sum(self, table, pokemon_id, start_date, end_date) -> int:
        area_placeholders = ", ".join(["%s"] * len(_STATS_AREAS))
        query = (
            f"SELECT COALESCE(SUM(count), 0) AS total FROM {table} "
            f"WHERE date BETWEEN %s AND %s AND area IN ({area_placeholders})"
        )
        params = [start_date, end_date, *_STATS_AREAS]
        if pokemon_id is not None:
            query += " AND pokemon_id = %s"
            params.append(pokemon_id)
        rows = await self.poliswag.quest_search.db.get_data_from_database(
            query, params=tuple(params)
        )
        return int(rows[0]["total"]) if rows else 0
