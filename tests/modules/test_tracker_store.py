from datetime import datetime
from unittest.mock import MagicMock

import pytest

from modules.tracker_store import TrackerStore


@pytest.fixture
def db():
    return MagicMock()


@pytest.fixture
def tracker_store(db):
    return TrackerStore(db)


class TestGetAll:
    def test_returns_rows_from_db_ordered_by_createddate_desc(self, tracker_store, db):
        rows = [
            {"target": "charmander", "creator": "alice", "createddate": "2024-01-01"}
        ]
        db.get_data_from_database.return_value = rows
        result = tracker_store.get_all()
        assert result is rows
        db.get_data_from_database.assert_called_once()
        sql = db.get_data_from_database.call_args.args[0]
        assert "FROM tracked_quest_reward" in sql
        assert "ORDER BY createddate DESC" in sql


class TestExists:
    def test_returns_true_when_row_present(self, tracker_store, db):
        db.get_data_from_database.return_value = [{"target": "pikachu"}]
        assert tracker_store.exists("pikachu") is True

    def test_returns_false_when_no_rows(self, tracker_store, db):
        db.get_data_from_database.return_value = []
        assert tracker_store.exists("missingno") is False

    def test_passes_target_as_parametrised_query(self, tracker_store, db):
        db.get_data_from_database.return_value = []
        tracker_store.exists("pikachu")
        call = db.get_data_from_database.call_args
        assert "WHERE target = %s" in call.args[0]
        assert call.kwargs["params"] == ("pikachu",)


class TestAdd:
    def test_insert_sql_and_params(self, tracker_store, db, mocker):
        fixed_now = datetime(2026, 4, 7, 12, 0, 0)
        mocker.patch("modules.tracker_store.datetime").now.return_value = fixed_now

        tracker_store.add("charizard", "alice")

        call = db.execute_query_to_database.call_args
        assert "INSERT INTO tracked_quest_reward" in call.args[0]
        assert call.kwargs["params"] == ("charizard", "alice", fixed_now)


class TestRemove:
    def test_delegates_rowcount_from_execute(self, tracker_store, db):
        db.execute_query_to_database.return_value = 1
        assert tracker_store.remove("pikachu") == 1
        call = db.execute_query_to_database.call_args
        assert "DELETE FROM tracked_quest_reward WHERE target = %s" in call.args[0]
        assert call.kwargs["params"] == ("pikachu",)

    def test_returns_zero_when_no_rows_affected(self, tracker_store, db):
        db.execute_query_to_database.return_value = 0
        assert tracker_store.remove("ghost") == 0


class TestClear:
    def test_returns_count_before_delete(self, tracker_store, db):
        db.get_data_from_database.return_value = [{"count": 5}]
        assert tracker_store.clear() == 5
        # Verify the DELETE was actually issued afterwards
        delete_sql = db.execute_query_to_database.call_args.args[0]
        assert "DELETE FROM tracked_quest_reward" in delete_sql

    def test_empty_table_returns_zero(self, tracker_store, db):
        db.get_data_from_database.return_value = []
        assert tracker_store.clear() == 0
        assert db.execute_query_to_database.called
