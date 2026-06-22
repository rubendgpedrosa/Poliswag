"""Tests for modules.lure_manager.LureManager.

LureManager opens a dragonite DatabaseConnector in __init__; we patch it at
the import site and use poliswag.db (a MagicMock) for the account_lure table.
After construction we replace dragonite_db with a MagicMock too.
"""

from unittest.mock import MagicMock, patch

import pytest

from modules.lure_manager import LureManager, DEFAULT_LURE_COUNT, MAX_LISTED


@pytest.fixture
def manager():
    poliswag = MagicMock()
    poliswag.db = MagicMock()
    with patch("modules.lure_manager.DatabaseConnector"):
        m = LureManager(poliswag)
    m.dragonite_db = MagicMock()
    # default: no rows unless a test sets them
    poliswag.db.get_data_from_database.return_value = []
    m.dragonite_db.get_data_from_database.return_value = []
    return m


class TestAvailableQuery:
    def test_query_filters_health_cooldown_and_selection(self, manager):
        manager.list_available_with_lures()
        sql = manager.dragonite_db.get_data_from_database.call_args.args[0]
        assert "FROM account" in sql
        assert "banned = 0" in sql
        assert "suspended = 0" in sql
        assert "invalid = 0" in sql
        assert "warn = 0" in sql
        assert "auth_banned = 0" in sql
        assert "next_available_time" in sql
        assert "last_released >= last_selected" in sql


class TestSeeding:
    def test_seeds_missing_usernames_at_default_count(self, manager):
        manager.dragonite_db.get_data_from_database.return_value = [
            {"username": "free_new", "password": "pw"},
        ]
        # account_lure currently empty (first poliswag call), then select returns nothing
        manager.db.get_data_from_database.side_effect = [[], []]
        manager.list_available_with_lures()
        insert_call = manager.db.execute_query_to_database.call_args
        assert "INSERT INTO account_lure" in insert_call.args[0]
        assert insert_call.kwargs["params"] == ("free_new", DEFAULT_LURE_COUNT)

    def test_does_not_seed_existing_usernames(self, manager):
        manager.dragonite_db.get_data_from_database.return_value = [
            {"username": "free_low", "password": "pw"},
        ]
        manager.db.get_data_from_database.side_effect = [
            [{"username": "free_low"}],  # existing account_lure rows
            [{"username": "free_low", "nb_lures": 2}],  # selection
        ]
        manager.list_available_with_lures()
        manager.db.execute_query_to_database.assert_not_called()


class TestListing:
    def test_merges_password_and_count_sorted_and_capped(self, manager):
        # 6 available accounts; all already seeded
        avail = [{"username": f"u{i}", "password": f"p{i}"} for i in range(6)]
        manager.dragonite_db.get_data_from_database.return_value = avail
        existing = [{"username": f"u{i}"} for i in range(6)]
        # selection returns up to MAX_LISTED rows, fewest-first, nb_lures > 0
        selected = [
            {"username": "u3", "nb_lures": 1},
            {"username": "u0", "nb_lures": 4},
            {"username": "u5", "nb_lures": 6},
            {"username": "u1", "nb_lures": 8},
            {"username": "u2", "nb_lures": 12},
        ]
        manager.db.get_data_from_database.side_effect = [existing, selected]
        result = manager.list_available_with_lures()
        assert [r["username"] for r in result] == ["u3", "u0", "u5", "u1", "u2"]
        assert result[0] == {"username": "u3", "password": "p3", "nb_lures": 1}
        # selection SQL caps at MAX_LISTED and sorts ascending
        sel_sql = manager.db.get_data_from_database.call_args.args[0]
        assert "nb_lures > 0" in sel_sql
        assert "ORDER BY nb_lures ASC" in sel_sql
        assert f"LIMIT {MAX_LISTED}" in sel_sql

    def test_returns_empty_when_no_available_accounts(self, manager):
        manager.dragonite_db.get_data_from_database.return_value = []
        result = manager.list_available_with_lures()
        assert result == []
        # no poliswag selection query issued when nothing is available
        manager.db.get_data_from_database.assert_not_called()


class TestAdjust:
    def test_update_floors_at_zero_and_returns_rowcount(self, manager):
        manager.db.execute_query_to_database.return_value = 1
        affected = manager.adjust_lure_count("free_low", -3)
        assert affected == 1
        call = manager.db.execute_query_to_database.call_args
        assert "UPDATE account_lure" in call.args[0]
        assert "GREATEST(nb_lures + %s, 0)" in call.args[0]
        assert call.kwargs["params"] == (-3, "free_low")

    def test_unknown_username_returns_zero(self, manager):
        manager.db.execute_query_to_database.return_value = 0
        assert manager.adjust_lure_count("ghost", 5) == 0
