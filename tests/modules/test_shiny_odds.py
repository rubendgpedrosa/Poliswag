"""Shiny odds that set themselves: reading a species' odds off the scanner's
own counts, and learning which species never come up shiny. The counts in the
estimate tests are the scanner's real ones from October 2026."""

from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from modules import shiny_odds
from modules.shiny_odds import ShinyOdds, collapsed, decide, estimate

NOON = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
HALF_DAY = 12 * 3600


class TestEstimate:
    def test_a_community_day_reads_as_one_in_25(self):
        assert estimate(23055, 915) == 25  # Zorua, 2026-10-10

    def test_a_boosted_event_reads_as_one_in_128(self):
        assert estimate(7527, 56) == 128  # Slugma, 2026-10-03
        assert estimate(9016, 71) == 128  # Smoliv, 2026-10-04

    def test_normal_odds_are_no_estimate(self):
        assert estimate(119185, 230) is None  # every species, an ordinary day
        assert estimate(8309, 26) is None  # Snorlax over a week, 1 in 320

    def test_a_lucky_run_is_not_an_event(self):
        assert estimate(156, 1) is None  # Zorua the day before its event
        assert estimate(60, 4) is None  # too few shinies to say anything
        assert estimate(5859, 23) is None  # 1 in 255 over a week: not enough

    def test_it_takes_a_hundred_or_so_encounters_at_community_day_odds(self):
        assert estimate(125, 5) == 25

    def test_no_shinies_or_no_encounters(self):
        assert estimate(5000, 0) is None
        assert estimate(0, 0) is None

    def test_counts_that_cannot_be_are_no_estimate(self):
        assert estimate(3, 7) is None


class TestCollapsed:
    def test_a_species_still_seen_at_the_days_pace_has_not(self):
        # 3,750 by noon is 156 per half hour.
        assert not collapsed(100, 3750, HALF_DAY)

    def test_a_species_that_all_but_stopped_appearing_has(self):
        # Zorua two hours after its Community Day: 23,000 today, 5 just now.
        assert collapsed(5, 23000, 19 * 3600)

    def test_the_first_minutes_of_a_day_count_as_a_full_window(self):
        assert not collapsed(40, 60, 120)


class TestDecide:
    def test_what_is_on_the_map_now_decides_by_itself(self):
        assert decide({570: (2000, 80)}, {570: (2150, 81)}, 14 * 3600) == {570: 25}

    def test_the_days_count_speaks_when_the_map_holds_too_few(self):
        assert decide({218: (150, 1)}, {218: (3750, 28)}, HALF_DAY) == {218: 128}

    def test_the_days_count_is_ignored_once_the_species_stopped_appearing(self):
        assert decide({570: (5, 0)}, {570: (23055, 915)}, 19 * 3600) == {}

    def test_a_species_seen_today_but_not_now_is_over(self):
        assert decide({}, {570: (23055, 915)}, 19 * 3600) == {}

    def test_species_at_normal_odds_are_left_out(self):
        assert decide({16: (300, 1)}, {16: (5000, 9)}, HALF_DAY) == {}


@pytest.fixture
def db():
    d = MagicMock()
    d.get_data_from_database = AsyncMock(return_value=[])
    d.execute_query_to_database = AsyncMock(return_value=0)
    d.execute_transaction = AsyncMock()
    return d


@pytest.fixture
def odds(db):
    bot = MagicMock()
    bot.db = db
    return ShinyOdds(bot)


def reads(db, live=(), day=(), through=date(2026, 10, 9)):
    """Route the three reads by what they select."""

    async def read(sql, params=None, **_):
        if "shiny_species_through" in sql:
            return [{"shiny_species_through": through}]
        if "pokemon_shiny_stats" in sql:
            return list(day)
        return list(live)

    db.get_data_from_database = AsyncMock(side_effect=read)


def counts(pokemon_id, encounters, shinies):
    return {"pokemon_id": pokemon_id, "encounters": encounters, "shinies": shinies}


class TestRates:
    async def test_a_detected_event_is_written_as_an_automatic_rate(self, odds, db):
        reads(db, live=[counts(570, 2000, 80)])
        await odds.tick(NOON)
        clear, write = db.execute_transaction.await_args.args[0]
        assert "DELETE FROM shiny_rate" in clear[0] and "source = 'auto'" in clear[0]
        assert "INSERT IGNORE INTO shiny_rate" in write[0] and "'auto'" in write[0]
        assert write[1] == (570, 25)

    async def test_a_rate_lapses_by_itself_if_the_bot_stops(self, odds, db):
        reads(db, live=[counts(570, 2000, 80)])
        await odds.tick(NOON)
        _, write = db.execute_transaction.await_args.args[0]
        assert "INTERVAL" in write[0] and "UTC_TIMESTAMP()" in write[0]

    async def test_a_rate_set_by_hand_is_left_alone_while_it_runs(self, odds, db):
        reads(db)
        await odds.tick(NOON)
        (clear,) = db.execute_transaction.await_args.args[0]
        # Only automatic rows and hand-set ones past their end are removed; the
        # INSERT IGNORE above then cannot replace a hand-set row still running.
        assert "source = 'auto' OR ends_at <= UTC_TIMESTAMP()" in clear[0]

    async def test_an_event_that_ended_is_dropped_on_the_next_tick(self, odds, db):
        reads(db, live=[counts(570, 2000, 80)])
        await odds.tick(NOON)
        reads(db, live=[counts(570, 4, 0)], day=[counts(570, 23055, 915)])
        await odds.tick(NOON.replace(hour=19))
        assert len(db.execute_transaction.await_args.args[0]) == 1  # the clear only

    async def test_the_map_is_read_half_an_hour_back_and_the_day_in_utc(self, odds, db):
        reads(db)
        await odds.tick(NOON)
        by_sql = {
            c.args[0]: c.kwargs.get("params")
            for c in db.get_data_from_database.call_args_list
        }
        (live_sql,) = [s for s in by_sql if "first_seen_timestamp" in s]
        (day_sql,) = [s for s in by_sql if "pokemon_shiny_stats" in s]
        assert "seen_type = 'encounter'" in live_sql and "shiny IS NOT NULL" in live_sql
        assert by_sql[live_sql] == (int(NOON.timestamp()) - shiny_odds.LIVE_WINDOW,)
        assert by_sql[day_sql] == (date(2026, 10, 10),)

    async def test_a_change_is_logged_once(self, odds, db):
        reads(db, live=[counts(570, 2000, 80)])
        await odds.tick(NOON)
        await odds.tick(NOON)
        odds.poliswag.utility.log_to_file.assert_called_once()
        assert "570" in odds.poliswag.utility.log_to_file.call_args.args[0]


class TestSpecies:
    async def test_finished_days_are_added_once(self, odds, db):
        reads(db, through=date(2026, 10, 7))
        await odds.tick(NOON)
        fold, mark = db.execute_transaction.await_args_list[-1].args[0]
        assert "INSERT INTO shiny_species" in fold[0]
        assert "encounters = encounters + VALUES(encounters)" in fold[0]
        # Days after the last one added, up to and not including today.
        assert fold[1] == (date(2026, 10, 7), date(2026, 10, 10))
        assert "shiny_species_through" in mark[0] and mark[1] == (date(2026, 10, 9),)

    async def test_nothing_is_added_when_yesterday_already_is(self, odds, db):
        reads(db, through=date(2026, 10, 9))
        await odds.tick(NOON)
        assert db.execute_transaction.await_count == 1  # the rates only

    async def test_the_first_run_takes_every_finished_day_the_scanner_keeps(
        self, odds, db
    ):
        reads(db, through=None)
        await odds.tick(NOON)
        fold, _ = db.execute_transaction.await_args_list[-1].args[0]
        assert fold[1] == (date.min, date(2026, 10, 10))

    async def test_yesterday_waits_an_hour_for_the_scanners_last_counts(self, odds, db):
        reads(db, through=date(2026, 10, 8))
        await odds.tick(NOON.replace(hour=0, minute=30))
        assert db.execute_transaction.await_count == 1  # the rates only

    async def test_never_shiny_counts_days_not_yet_added(self, odds, db):
        reads(db, through=date(2026, 10, 9))
        await odds.tick(NOON)
        sql = db.execute_query_to_database.await_args.args[0]
        params = db.execute_query_to_database.await_args.kwargs["params"]
        assert "UPDATE shiny_species" in sql and "never_shiny" in sql
        # A species seen shiny today for the first time is predicted at once.
        assert "pokemon_shiny_stats" in sql and "`count` > 0" in sql
        assert params == (date(2026, 10, 9), shiny_odds.NEVER_AFTER)
