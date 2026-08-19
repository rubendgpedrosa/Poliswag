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
    w.poliswag.quest_search.masterfile_data = None
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
    def test_falls_back_to_hardcoded_name_when_no_masterfile(self, watcher):
        watcher.poliswag.quest_search.masterfile_data = None
        assert watcher._lure_name(505) == "Lure Chuvoso"

    def test_unknown_id_falls_back_to_generic_lure(self, watcher):
        watcher.poliswag.quest_search.masterfile_data = None
        assert watcher._lure_name(999) == "Lure"

    def test_prefers_masterfile_dict_entry(self, watcher):
        watcher.poliswag.quest_search.masterfile_data = {
            "items": {"505": {"name": "Chuva Mágica"}}
        }
        assert watcher._lure_name(505) == "Chuva Mágica"

    def test_prefers_masterfile_string_entry(self, watcher):
        watcher.poliswag.quest_search.masterfile_data = {"items": {"505": "Chuva"}}
        assert watcher._lure_name(505) == "Chuva"

    def test_missing_item_id_falls_back_to_hardcoded_name(self, watcher):
        watcher.poliswag.quest_search.masterfile_data = {"items": {}}
        assert watcher._lure_name(505) == "Lure Chuvoso"


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
        assert result[0]["lure_name"] == "Lure Chuvoso"

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
