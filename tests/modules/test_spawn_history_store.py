from unittest.mock import MagicMock

import pytest

from modules import spawn_history


@pytest.mark.parametrize("hours", [0, 49, -1, True, "24", 1.5, None])
def test_invalid_period_never_opens_connection(monkeypatch, hours):
    connection = MagicMock()
    monkeypatch.setattr(spawn_history, "connect", connection)
    for function in (
        spawn_history.status,
        spawn_history.export_json,
        spawn_history.leaderboard,
    ):
        with pytest.raises(ValueError):
            function(hours)
    connection.assert_not_called()


def test_failed_collector_rolls_back_and_closes(monkeypatch):
    connection = MagicMock()
    connection.cursor.return_value.__enter__.return_value.execute.side_effect = (
        RuntimeError("failure")
    )
    monkeypatch.setattr(spawn_history, "connect", lambda *args, **kwargs: connection)
    with pytest.raises(RuntimeError):
        spawn_history.capture()
    connection.rollback.assert_called_once()
    connection.close.assert_called_once()
    connection.commit.assert_not_called()


def test_first_poll_is_not_historical_coverage():
    cursor = MagicMock()
    cursor.fetchall.side_effect = [[{"started_at": 1000, "last_capture_at": 1000}], []]
    assert not spawn_history.window_metadata(cursor, 24)["complete_window"]


def test_no_archive_is_unavailable():
    cursor = MagicMock()
    cursor.fetchall.return_value = []
    assert spawn_history.window_metadata(cursor, 24) == {
        "available": False,
        "complete_window": False,
        "hours": 24,
    }
