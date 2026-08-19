"""Tests for modules.lure_watcher.LureWatcher.

golbat's pokestop table only records that a lure is active (lure_id,
lure_expire_timestamp), never who placed it. check_new_lures() diffs the
current active-lure snapshot against what it saw last tick to detect fresh
placements, and never announces anything already active on its first run
(startup must not spam the existing backlog).
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from modules.lure_watcher import LureWatcher


@pytest.fixture
def watcher():
    w = LureWatcher(poliswag=MagicMock())
    w.poliswag.quest_search.db = AsyncMock()
    return w


def _row(
    pid="stop1", name="Anfiteatro", lat=39.7175, lon=-8.8022, lure_id=505, expiry=1000
):
    return {
        "id": pid,
        "name": name,
        "lat": lat,
        "lon": lon,
        "lure_id": lure_id,
        "lure_expire_timestamp": expiry,
    }


class TestInit:
    def test_starts_unseeded_with_empty_state(self):
        w = LureWatcher(poliswag=MagicMock())
        assert w._known_lure_expiry == {}
        assert w._seeded is False


class TestLureName:
    def test_known_lure_id_returns_in_game_english_name(self, watcher):
        assert watcher._lure_name(505) == "Rainy Lure"

    def test_unknown_id_falls_back_to_generic_lure(self, watcher):
        assert watcher._lure_name(999) == "Lure"


class TestArea:
    def test_at_or_west_of_threshold_is_marinha_grande(self, watcher):
        assert watcher._area(-8.9) == "Marinha Grande"
        assert watcher._area(-9.0) == "Marinha Grande"

    def test_east_of_threshold_is_leiria(self, watcher):
        assert watcher._area(-8.8) == "Leiria"


class TestCheckNewLures:
    async def test_first_run_seeds_silently(self, watcher):
        watcher.poliswag.quest_search.db.get_data_from_database.return_value = [
            _row(expiry=1000)
        ]
        result = await watcher.check_new_lures()
        assert result == []
        assert watcher._seeded is True
        assert watcher._known_lure_expiry == {"stop1": 1000}

    async def test_unchanged_lure_is_not_reported(self, watcher):
        watcher.poliswag.quest_search.db.get_data_from_database.return_value = [
            _row(expiry=1000)
        ]
        await watcher.check_new_lures()  # seed
        result = await watcher.check_new_lures()  # same state next tick
        assert result == []

    async def test_fresh_lure_on_a_new_stop_is_reported(self, watcher):
        watcher.poliswag.quest_search.db.get_data_from_database.return_value = []
        await watcher.check_new_lures()  # seed with nothing active

        watcher.poliswag.quest_search.db.get_data_from_database.return_value = [
            _row(expiry=2000)
        ]
        result = await watcher.check_new_lures()
        assert len(result) == 1
        assert result[0]["name"] == "Anfiteatro"
        assert result[0]["lure_name"] == "Rainy Lure"
        assert result[0]["area"] == "Leiria"

    async def test_relured_stop_with_later_expiry_is_reported(self, watcher):
        watcher.poliswag.quest_search.db.get_data_from_database.return_value = [
            _row(expiry=1000)
        ]
        await watcher.check_new_lures()  # seed

        watcher.poliswag.quest_search.db.get_data_from_database.return_value = [
            _row(expiry=5000)
        ]
        result = await watcher.check_new_lures()
        assert len(result) == 1
        assert watcher._known_lure_expiry == {"stop1": 5000}

    async def test_stop_missing_a_name_falls_back_to_generic_label(self, watcher):
        watcher.poliswag.quest_search.db.get_data_from_database.return_value = []
        await watcher.check_new_lures()  # seed

        watcher.poliswag.quest_search.db.get_data_from_database.return_value = [
            _row(name=None, expiry=2000)
        ]
        result = await watcher.check_new_lures()
        assert result[0]["name"] == "PokéStop"

    async def test_expired_stop_drops_out_of_known_state(self, watcher):
        watcher.poliswag.quest_search.db.get_data_from_database.return_value = [
            _row(expiry=1000)
        ]
        await watcher.check_new_lures()  # seed

        watcher.poliswag.quest_search.db.get_data_from_database.return_value = []
        result = await watcher.check_new_lures()
        assert result == []
        assert watcher._known_lure_expiry == {}

    async def test_db_error_is_logged_and_returns_empty_list(self, watcher):
        watcher.poliswag.quest_search.db.get_data_from_database.side_effect = (
            RuntimeError("db down")
        )
        result = await watcher.check_new_lures()
        assert result == []
        watcher.poliswag.utility.log_to_file.assert_called_once()


class TestCountActiveLures:
    async def test_returns_count_from_db(self, watcher):
        watcher.poliswag.quest_search.db.get_data_from_database.return_value = [
            {"active": 4}
        ]
        assert await watcher.count_active_lures() == 4

    async def test_empty_rows_returns_zero(self, watcher):
        watcher.poliswag.quest_search.db.get_data_from_database.return_value = []
        assert await watcher.count_active_lures() == 0

    async def test_db_error_is_logged_and_returns_zero(self, watcher):
        watcher.poliswag.quest_search.db.get_data_from_database.side_effect = (
            RuntimeError("db down")
        )
        assert await watcher.count_active_lures() == 0
        watcher.poliswag.utility.log_to_file.assert_called_once()
