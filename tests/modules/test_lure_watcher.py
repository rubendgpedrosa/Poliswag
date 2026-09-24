"""Tests for modules.lure_watcher.LureWatcher."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from modules.lure_watcher import LureWatcher


@pytest.fixture
def watcher():
    w = LureWatcher(poliswag=MagicMock())
    w.poliswag.quest_search.db = AsyncMock()
    return w


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
