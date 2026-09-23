import datetime
import json
import re

from modules.logging_mixin import LoggingMixin

# Golbat's *_stats tables (populated by golbat itself, not written by
# poliswag) are date-granularity, not per-minute -- there's no way to
# isolate exactly the event's hours from the rest of that day. For
# Community Day / Raid Day / Raid Hour (a few boosted hours on one day),
# the boosted activity dominates the day's total, so the daily sum is a
# reasonable proxy; it's not exact for events that share a day with
# unrelated activity. Spotlight Hour is the weakest fit here (just one
# boosted hour vs. the rest of the day), but still the best signal
# available without per-minute stats.
#
# Golbat keeps about a week of these rows, so the comparison is with the
# day before the event rather than the same weekday last week: that one is
# sometimes already gone, and last Wednesday was a Raid Hour too.
_COMMUNITY_DAY_SUFFIX = re.compile(r"\s*community day\s*$", re.IGNORECASE)
_SPOTLIGHT_HOUR_SUFFIX = re.compile(r"\s*spotlight hour\s*$", re.IGNORECASE)
_RAID_HOUR_OR_DAY = re.compile(r"raid[- ](hour|day)", re.IGNORECASE)
# ScrapedDuck's species icons come in two namings: pokemon_icon_443_00.png
# is Gible, pm4.fGOGGLES_2026.icon.png a Charmander in costume.
_ICON_POKEMON_ID = re.compile(r"pokemon_icon_(\d+)_|/pm(\d+)\.")
_STATS_AREAS = ("Leiria", "MarinhaGrande")
_AREA_LABELS = dict(zip(_STATS_AREAS, ("Leiria", "Marinha Grande")))
_BOSSES_SHOWN = 3

# Golbat's raid levels. Tier names are game terms and stay in English.
_RAID_LEVELS = {
    1: "1★",
    3: "3★",
    5: "5★",
    6: "Mega",
    7: "Mega Legendary",
    8: "Ultra Beast",
    9: "Elite",
    10: "Primal",
    11: "Shadow 1★",
    12: "Shadow 2★",
    13: "Shadow 3★",
    14: "Shadow 4★",
    15: "Shadow 5★",
    16: "Super Mega",
}


class EventStats(LoggingMixin):
    """Post-event stats summaries, sourced from golbat's own aggregate
    stats tables (raid_stats, pokemon_stats, pokemon_hundo_stats,
    pokemon_nundo_stats)."""

    def __init__(self, poliswag):
        self.poliswag = poliswag

    async def get_summary(self, event: dict) -> str | None:
        """A short PT-PT stats text for a finished event, or None when there
        is nothing to say: an event type with no stats source (GO Battle
        League, research, a week-long raid rotation), a featured species that
        can't be identified, or no rows at all."""
        event_type = (event.get("event_type") or "").lower()
        try:
            start_date = str(event["start"])[:10]
            end_date = str(event["end"])[:10]
        except (KeyError, TypeError):
            return None

        try:
            if _RAID_HOUR_OR_DAY.search(event_type):
                return await self._raid_summary(start_date, end_date)
            if "community" in event_type:
                return await self._species_summary(
                    event, _COMMUNITY_DAY_SUFFIX, start_date, end_date
                )
            if "spotlight" in event_type:
                return await self._species_summary(
                    event, _SPOTLIGHT_HOUR_SUFFIX, start_date, end_date
                )
        except Exception as e:
            self._log(f"[EVENTSTATS] Failed to build summary: {e}")
            return None
        return None

    @staticmethod
    def _number(value):
        return f"{value:,}".replace(",", " ")

    @staticmethod
    def _period_note(start_date, end_date):
        period = start_date if start_date == end_date else f"{start_date} a {end_date}"
        return f"_Totais diários · {period} · incluem atividade fora do horário do evento._"

    @staticmethod
    def _day_before(start_date):
        day = datetime.date.fromisoformat(start_date) - datetime.timedelta(days=1)
        return day.isoformat()

    def _versus(self, total, before):
        """ "📈 3,3× o dia anterior (77)", or None with nothing to compare to."""
        if not before:
            return None
        ratio = f"{total / before:.1f}".replace(".", ",").removesuffix(",0")
        arrow = "📈" if total >= before else "📉"
        return f"{arrow} **{ratio}×** o dia anterior ({self._number(before)})"

    async def _by_area(self, table, pokemon_ids, start_date, end_date):
        placeholders = ", ".join(["%s"] * len(_STATS_AREAS))
        query = (
            f"SELECT area, SUM(count) AS total FROM {table} "
            f"WHERE date BETWEEN %s AND %s AND area IN ({placeholders})"
        )
        params = [start_date, end_date, *_STATS_AREAS]
        if pokemon_ids is not None:
            query += f" AND pokemon_id IN ({', '.join(['%s'] * len(pokemon_ids))})"
            params.extend(pokemon_ids)
        query += " GROUP BY area"
        rows = await self.poliswag.quest_search.db.get_data_from_database(
            query, params=tuple(params)
        )
        return {row["area"]: int(row["total"]) for row in rows}

    async def _raids(self, start_date, end_date):
        """[(area, level, pokemon_id, total)] for the stats areas."""
        placeholders = ", ".join(["%s"] * len(_STATS_AREAS))
        rows = await self.poliswag.quest_search.db.get_data_from_database(
            "SELECT area, level, pokemon_id, SUM(count) AS total FROM raid_stats "
            f"WHERE date BETWEEN %s AND %s AND area IN ({placeholders}) "
            "GROUP BY area, level, pokemon_id",
            params=(start_date, end_date, *_STATS_AREAS),
        )
        return [
            (row["area"], int(row["level"]), int(row["pokemon_id"]), int(row["total"]))
            for row in rows
        ]

    async def _raid_summary(self, start_date, end_date) -> str | None:
        """The raids the event was about, against the day before.

        A day's raids are every tier at once, so the plain total said little:
        a Raid Hour's 250 Zamazenta vanished into the 1★ and 3★ around them.
        The event's tier is the one that grew most over the day before, which
        needs no parsing of "Xurkitree, Pheromosa, and Buzzwole Raid Hour".
        """
        rows = await self._raids(start_date, end_date)
        if not rows:
            return None
        day_before = self._day_before(start_date)
        before_rows = await self._raids(day_before, day_before)

        by_level, before_by_level = {}, {}
        for _area, level, _pid, total in rows:
            by_level[level] = by_level.get(level, 0) + total
        for _area, level, _pid, total in before_rows:
            before_by_level[level] = before_by_level.get(level, 0) + total
        level = max(
            by_level,
            key=lambda lv: (by_level[lv] - before_by_level.get(lv, 0), by_level[lv]),
        )

        bosses, areas = {}, {}
        for area, lv, pid, total in rows:
            if lv != level:
                continue
            bosses[pid] = bosses.get(pid, 0) + total
            areas[area] = areas.get(area, 0) + total
        ranked = sorted(bosses, key=bosses.get, reverse=True)
        names = ", ".join(self._pokemon_name(pid) for pid in ranked[:_BOSSES_SHOWN])
        if len(ranked) > _BOSSES_SHOWN:
            names += f" +{len(ranked) - _BOSSES_SHOWN}"

        tier = _RAID_LEVELS.get(level, f"nível {level}")
        lines = [f"🥊 **{self._number(by_level[level])}** raids {tier} · {names}"]
        versus = self._versus(by_level[level], before_by_level.get(level, 0))
        if versus:
            lines.append(versus)
        for area in _STATS_AREAS:
            value = self._number(areas[area]) if area in areas else "sem dados"
            lines.append(f"**{_AREA_LABELS[area]}:** {value}")
        return "\n".join(lines) + "\n\n" + self._period_note(start_date, end_date)

    async def _species_summary(
        self, event, suffix_pattern, start_date, end_date
    ) -> str | None:
        featured = self._featured_species(event, suffix_pattern)
        if not featured:
            return None
        ids = [pid for pid, _name in featured]
        spawns = await self._by_area("pokemon_stats", ids, start_date, end_date)
        if not spawns:
            return None
        hundos = await self._by_area("pokemon_hundo_stats", ids, start_date, end_date)
        nundos = await self._by_area("pokemon_nundo_stats", ids, start_date, end_date)
        day_before = self._day_before(start_date)
        before = await self._by_area("pokemon_stats", ids, day_before, day_before)

        total = sum(spawns.values())
        species = ", ".join(name for _pid, name in featured)
        lines = [f"🐾 **{self._number(total)}** spawns · {species}"]
        versus = self._versus(total, sum(before.values()))
        if versus:
            lines.append(versus)
        for area in _STATS_AREAS:
            label = _AREA_LABELS[area]
            if area not in spawns:
                lines.append(f"**{label}:** sem dados")
                continue
            lines.append(
                f"**{label}:** {self._number(spawns[area])} spawns · "
                f"💯 {self._number(hundos.get(area, 0))} 100IV · "
                f"0️⃣ {self._number(nundos.get(area, 0))} 0IV"
            )
        return "\n".join(lines) + "\n\n" + self._period_note(start_date, end_date)

    def _featured_species(self, event, suffix_pattern):
        """[(pokemon_id, name)] the event features.

        ScrapedDuck lists them, with the dex number in each icon's file name.
        The event's name was the only source before, and it only held a
        species for the simple cases: "Houndour and Houndoom", "Charmander
        wearing Friede's goggles" and "Gible Community Day Classic" all came
        out empty. The name stays as the fallback for an event stored
        without extra data.
        """
        featured = self._species_from_extra(event.get("extra_data"))
        if featured:
            return featured
        species = suffix_pattern.sub("", event.get("name", "")).strip()
        if not species:
            return []
        pokemon_id = self._resolve_pokemon_id(species)
        return [] if pokemon_id is None else [(pokemon_id, species)]

    @staticmethod
    def _species_from_extra(extra_data):
        try:
            data = json.loads(extra_data) if isinstance(extra_data, str) else extra_data
        except ValueError:
            return []
        extra = (data or {}).get("extraData") or {}
        entries = list((extra.get("communityday") or {}).get("spawns") or [])
        spotlight = extra.get("spotlight") or {}
        entries += spotlight.get("list") or ([spotlight] if spotlight else [])

        featured = []
        for entry in entries:
            match = _ICON_POKEMON_ID.search(entry.get("image") or "")
            if not match:
                continue
            pokemon_id = int(match.group(1) or match.group(2))
            if pokemon_id not in [pid for pid, _name in featured]:
                featured.append((pokemon_id, entry.get("name") or f"#{pokemon_id}"))
        return featured

    def _pokemon_name(self, pokemon_id):
        name = self.poliswag.quest_search.pokemon_name_map.get(str(pokemon_id))
        return name.title() if name else f"#{pokemon_id}"

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
