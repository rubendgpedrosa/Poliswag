"""Release form identity, dates, exact costumes and source refresh resilience."""

from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

from modules.shiny_availability import ShinyAvailability, released_forms

MASTER = {
    "pokemon": {
        "25": {
            "name": "Pikachu",
            "defaultFormId": 598,
            "forms": {"598": {"name": "Normal"}, "894": {"name": "Fall 2019"}},
        },
        "201": {
            "name": "Unown",
            "defaultFormId": 1,
            "forms": {"1": {"name": "A"}, "11": {"name": "K"}},
        },
    }
}


def entry(token="pm25", released="2020/01/01", pid=25):
    return {"dex": pid, "aa_fn": token, "released_date": released}


def test_species_release_enables_only_its_default_and_absent_form_alias():
    assert released_forms([entry()], {"25": {"found_wild": True}}, MASTER) == [
        (25, 0, 0, date(2020, 1, 1), 1),
        (25, 598, 0, date(2020, 1, 1), 1),
    ]


def test_named_form_does_not_enable_ordinary_form_or_another_costume():
    rows = released_forms(
        [entry("pm25.fFALL_2019")], {"25": {"found_wild": True}}, MASTER
    )
    assert rows == [(25, 894, 0, date(2020, 1, 1), 1)]


@pytest.mark.parametrize(
    "record",
    [
        entry("pm25.cSANTA"),
        entry("pm25.???"),
        entry("pm25.fUNKNOWN"),
        entry(released="soon"),
        entry(released="2020/02/30"),
        entry("pm26"),
        entry(pid=True),
    ],
)
def test_unmapped_or_invalid_variants_never_grant_access(record):
    assert released_forms([record], {"25": {"found_wild": True}}, MASTER) == []


def test_ambiguous_forms_are_skipped():
    master = {
        "pokemon": {
            "25": {
                **MASTER["pokemon"]["25"],
                "forms": {"1": {"name": "Fall 2019"}, "2": {"name": "Fall 2019"}},
            }
        }
    }
    assert not released_forms(
        [entry("pm25.fFALL_2019")], {"25": {"found_wild": True}}, master
    )


def test_future_dates_are_preserved_for_the_sql_date_gate():
    assert released_forms(
        [entry(released="2027/01/01")], {"25": {"found_wild": True}}, MASTER
    )[0][3] == date(2027, 1, 1)


@pytest.mark.parametrize(
    "wild", [{}, {"25": {"found_wild": False}}, {"25": {"found_wild": "true"}}]
)
def test_raid_egg_or_unknown_eligibility_does_not_grant_wild_predictions(wild):
    assert all(row[4] == 0 for row in released_forms([entry()], wild, MASTER))


def test_unown_form_and_event_restrictions_are_preserved():
    rows = released_forms(
        [entry("pm201.fUNOWN_A", pid=201)], {"201": {"found_wild": True}}, MASTER
    )
    assert rows == [(201, 1, 0, date(2020, 1, 1), 0)]


def test_duplicate_sources_keep_the_earliest_release():
    rows = released_forms(
        [entry(released="2027/01/01"), entry()], {"25": {"found_wild": True}}, MASTER
    )
    assert len(rows) == 2 and rows[0][3] == date(2020, 1, 1)


@pytest.fixture
def availability():
    bot = MagicMock()
    bot.db.execute_query_to_database = AsyncMock()
    bot.db.execute_transaction = AsyncMock()
    return ShinyAvailability(bot)


async def test_failed_refresh_preserves_existing_releases_and_retries_later(
    availability,
):
    availability._fetch = AsyncMock(side_effect=RuntimeError("offline"))
    await availability.tick()
    availability.db.execute_transaction.assert_not_awaited()
    availability.poliswag.utility.log_to_file.assert_called_once()
    await availability.tick()
    assert availability._fetch.await_count == 2
    assert availability.db.execute_query_to_database.await_count == 2


async def test_partial_sources_do_not_replace_the_previous_snapshot(availability):
    availability._fetch = AsyncMock(side_effect=[[], {}])
    await availability.tick()
    availability.db.execute_transaction.assert_not_awaited()


async def test_good_sources_replace_atomically_and_refresh_once_per_day(availability):
    pokemon = {}
    releases = []
    wild = {}
    for pid in range(1, 801):
        pokemon[str(pid)] = {
            "name": "Pokemon",
            "defaultFormId": pid,
            "forms": {str(pid): {"name": "Normal"}},
        }
        releases.append(entry(f"pm{pid}", pid=pid))
        wild[str(pid)] = {"found_wild": True}
    availability.poliswag.quest_search.masterfile_data = {"pokemon": pokemon}
    availability._fetch = AsyncMock(side_effect=[releases, wild])
    await availability.tick()
    statements = availability.db.execute_transaction.call_args.args[0]
    assert statements[0][0] == "DELETE FROM shiny_release"
    assert "synced_at" in statements[1][0]
    assert len(statements[1][1]) == 1600 * 5
    await availability.tick()
    assert availability._fetch.await_count == 2
    availability.db.execute_transaction.assert_awaited_once()
