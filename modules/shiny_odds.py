"""Shiny odds that set themselves, from the scanner's own counts.

Two jobs each scheduler tick:

1. Event odds. A species whose scanner accounts are finding it shiny far
   more often than 1 in 512 gets a row in shiny_rate (source 'auto') at the
   nearest odds the game is known to use; the row goes when the counts stop
   supporting it. A row set by hand wins while it runs.
2. Species that are never shiny. Golbat keeps a week of counts; finished days
   are added to shiny_species so the evidence keeps growing, and a species
   and form encountered NEVER_AFTER times without one shiny is marked
   never_shiny. One shiny, ever, unmarks it.

Owns shiny_rate's 'auto' rows, shiny_species and
poliswag.shiny_species_through; reads golbat's pokemon and
pokemon_shiny_stats (whose dates are UTC). The site's map reads both tables;
map and alerts share released/confirmed variant eligibility and the same override rates.
"""

import math
from datetime import datetime, timedelta, timezone, date

from modules.config import Config

BASE_ODDS = 512
# The odds the game is known to use for wild spawns: normal, the boosted
# steps, Community Day, and the 1 in 10 of a research or raid day.
TIERS = (BASE_ODDS, 256, 128, 64, 25, 10)
# An estimate is taken only when the counts are this many times likelier
# under it than under normal odds, and rest on at least this many shinies.
ADOPT = 1000
MIN_SHINIES = 5
# How far back the live map is read. Golbat deletes a spawn soon after it
# despawns, so this is about as far as its table goes.
LIVE_WINDOW = 1800
# The day's count stops speaking for a species seen at under this share of
# the day's own pace: its event is over, however good the day's odds were.
COLLAPSE = 0.25
# An automatic rate is rewritten every tick and lapses this long after the
# last one, so a stopped bot cannot leave event odds standing.
RATE_LIFETIME_MINUTES = 15
# Encounters without a single shiny before a species and form is taken as
# never shiny. At 1 in 512 a run this long happens once in 17,000.
NEVER_AFTER = 5000
# Yesterday is added to shiny_species only this long into today, once
# Golbat has written its last counts for it.
SETTLE = timedelta(hours=1)

_LIVE_SQL = f"""
SELECT pokemon_id, COUNT(*) AS encounters, SUM(shiny = 1) AS shinies
FROM {Config.DB_SCANNER_NAME}.pokemon
WHERE seen_type = 'encounter' AND shiny IS NOT NULL
  AND first_seen_timestamp >= %s
GROUP BY pokemon_id
"""
_DAY_SQL = f"""
SELECT pokemon_id, SUM(total) AS encounters, SUM(`count`) AS shinies
FROM {Config.DB_SCANNER_NAME}.pokemon_shiny_stats
WHERE date = %s
GROUP BY pokemon_id
"""
_CLEAR_RATES_SQL = (
    "DELETE FROM shiny_rate WHERE source = 'auto' OR ends_at <= UTC_TIMESTAMP()"
)
_SET_RATE_SQL = f"""
INSERT IGNORE INTO shiny_rate (pokemon_id, rate, ends_at, source)
VALUES (%s, %s, UTC_TIMESTAMP() + INTERVAL {RATE_LIFETIME_MINUTES} MINUTE, 'auto')
"""
_FOLD_SQL = f"""
INSERT INTO shiny_species (pokemon_id, form_id, encounters, shinies)
SELECT pokemon_id, form_id, SUM(total), SUM(`count`)
FROM {Config.DB_SCANNER_NAME}.pokemon_shiny_stats
WHERE date > %s AND date < %s
GROUP BY pokemon_id, form_id
ON DUPLICATE KEY UPDATE
  encounters = encounters + VALUES(encounters), shinies = shinies + VALUES(shinies)
"""
# The days not added yet (today, and yesterday for its first hour) still
# count for the one thing that matters at once: a first shiny.
_NEVER_SQL = f"""
UPDATE shiny_species s
LEFT JOIN (
  SELECT pokemon_id, form_id FROM {Config.DB_SCANNER_NAME}.pokemon_shiny_stats
  WHERE date > %s AND `count` > 0 GROUP BY pokemon_id, form_id
) recent ON recent.pokemon_id = s.pokemon_id AND recent.form_id = s.form_id
SET s.never_shiny = (s.shinies = 0 AND s.encounters >= %s AND recent.pokemon_id IS NULL)
"""


def _log_likelihood(encounters, shinies, odds):
    return shinies * math.log(1 / odds) + (encounters - shinies) * math.log1p(-1 / odds)


def estimate(encounters, shinies):
    """The odds (the N of 1 in N) these counts show, or None for normal odds
    or too little to tell. Always one of TIERS: a count can say which of the
    game's odds is in force, not invent one."""
    if not MIN_SHINIES <= shinies <= encounters:
        return None
    scores = {odds: _log_likelihood(encounters, shinies, odds) for odds in TIERS}
    best = max(TIERS, key=scores.get)
    if best == BASE_ODDS or scores[best] - scores[BASE_ODDS] < math.log(ADOPT):
        return None
    return best


def collapsed(live_encounters, day_encounters, seconds_today):
    """Has this species all but stopped appearing, next to its own day?"""
    expected = day_encounters * LIVE_WINDOW / max(seconds_today, LIVE_WINDOW)
    return live_encounters < COLLAPSE * expected


def decide(live, day, seconds_today):
    """{pokemon_id: odds} for the species on event odds right now.

    `live` and `day` map a species to (encounters, shinies): what is on the
    map now, and the day so far. The map decides when it holds enough; the
    day's count speaks otherwise, for as long as the species keeps appearing.
    """
    rates = {}
    for pokemon_id in live.keys() | day.keys():
        live_encounters, live_shinies = live.get(pokemon_id, (0, 0))
        day_encounters, day_shinies = day.get(pokemon_id, (0, 0))
        odds = estimate(live_encounters, live_shinies)
        if odds is None and not collapsed(
            live_encounters, day_encounters, seconds_today
        ):
            odds = estimate(day_encounters, day_shinies)
        if odds is not None:
            rates[pokemon_id] = odds
    return rates


def _counts(rows):
    return {
        row["pokemon_id"]: (int(row["encounters"] or 0), int(row["shinies"] or 0))
        for row in rows
    }


class ShinyOdds:
    def __init__(self, poliswag):
        self.poliswag = poliswag
        self.db = poliswag.db
        self._rates = {}

    async def tick(self, now=None):
        now = now or datetime.now(timezone.utc)
        await self._set_rates(now)
        await self._learn_species(now)

    async def _set_rates(self, now):
        live = _counts(
            await self.db.get_data_from_database(
                _LIVE_SQL, params=(int(now.timestamp()) - LIVE_WINDOW,)
            )
        )
        day = _counts(
            await self.db.get_data_from_database(_DAY_SQL, params=(now.date(),))
        )
        seconds_today = now.hour * 3600 + now.minute * 60 + now.second
        rates = decide(live, day, seconds_today)
        await self.db.execute_transaction(
            [
                (_CLEAR_RATES_SQL, None),
                *((_SET_RATE_SQL, item) for item in sorted(rates.items())),
            ]
        )
        if rates != self._rates:
            self.poliswag.utility.log_to_file(
                "Shiny odds read off the scanner's counts: "
                + (
                    ", ".join(f"#{p} 1/{o}" for p, o in sorted(rates.items()))
                    or "all normal"
                )
            )
            self._rates = rates

    async def _learn_species(self, now):
        rows = await self.db.get_data_from_database(
            "SELECT shiny_species_through FROM poliswag"
        )
        through = rows[0]["shiny_species_through"] if rows else None
        last_finished = (now - SETTLE).date() - timedelta(days=1)
        if through is None or through < last_finished:
            await self.db.execute_transaction(
                [
                    (
                        _FOLD_SQL,
                        (through or date.min, last_finished + timedelta(days=1)),
                    ),
                    (
                        "UPDATE poliswag SET shiny_species_through = %s",
                        (last_finished,),
                    ),
                ]
            )
            through = last_finished
        await self.db.execute_query_to_database(
            _NEVER_SQL, params=(through, NEVER_AFTER)
        )
