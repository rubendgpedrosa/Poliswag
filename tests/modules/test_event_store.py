from unittest.mock import MagicMock

import pytest

from modules.event_store import EventStore


@pytest.fixture
def db():
    return MagicMock()


@pytest.fixture
def event_store(db):
    return EventStore(db)


class TestGetExcludedTypes:
    def test_returns_db_rows_verbatim(self, event_store, db):
        rows = [{"type": "community_day"}, {"type": "raid_hour"}]
        db.get_data_from_database.return_value = rows
        assert event_store.get_excluded_types() is rows
        sql = db.get_data_from_database.call_args.args[0]
        assert "SELECT type FROM excluded_event_type" in sql


class TestGetAllEventTypes:
    def test_returns_db_rows_verbatim(self, event_store, db):
        rows = [{"event_type": "community_day"}]
        db.get_data_from_database.return_value = rows
        assert event_store.get_all_event_types() is rows
        sql = db.get_data_from_database.call_args.args[0]
        assert "FROM event" in sql
        assert "GROUP BY event_type" in sql


class TestIsExcluded:
    def test_returns_true_when_matching_row_found(self, event_store, db):
        db.get_data_from_database.return_value = [{"type": "community_day"}]
        assert event_store.is_excluded("community_day") is True

    def test_returns_false_when_no_rows(self, event_store, db):
        db.get_data_from_database.return_value = []
        assert event_store.is_excluded("anything") is False

    def test_uses_parametrised_query(self, event_store, db):
        db.get_data_from_database.return_value = []
        event_store.is_excluded("community_day")
        call = db.get_data_from_database.call_args
        assert "WHERE type = %s" in call.args[0]
        assert call.kwargs["params"] == ("community_day",)


class TestAddExcluded:
    def test_issues_insert_with_params(self, event_store, db):
        event_store.add_excluded("raid_hour")
        call = db.execute_query_to_database.call_args
        assert "INSERT INTO excluded_event_type" in call.args[0]
        assert call.kwargs["params"] == ("raid_hour",)


class TestRemoveExcluded:
    def test_delegates_execute_return_value(self, event_store, db):
        db.execute_query_to_database.return_value = 1
        assert event_store.remove_excluded("raid_hour") == 1
        call = db.execute_query_to_database.call_args
        assert "DELETE FROM excluded_event_type WHERE type = %s" in call.args[0]
        assert call.kwargs["params"] == ("raid_hour",)

    def test_returns_zero_when_no_rows_affected(self, event_store, db):
        db.execute_query_to_database.return_value = 0
        assert event_store.remove_excluded("nothing") == 0


class TestClearExcluded:
    def test_returns_previous_count(self, event_store, db):
        db.get_data_from_database.return_value = [{"count": 3}]
        assert event_store.clear_excluded() == 3
        delete_sql = db.execute_query_to_database.call_args.args[0]
        assert "DELETE FROM excluded_event_type" in delete_sql

    def test_returns_zero_on_empty_table(self, event_store, db):
        db.get_data_from_database.return_value = []
        assert event_store.clear_excluded() == 0
        assert db.execute_query_to_database.called
