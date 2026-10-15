from datetime import datetime
from unittest.mock import MagicMock

import pytest

from modules import shiny_access_requests as requests

STAMP = "20261015120000123456"


def player(**overrides):
    return {
        "discord_id": "123",
        "username": "trainer",
        "friend_code": "123456789012",
        "shiny_areas": "marinha",
        "shiny_relink": 0,
        "shiny_access_requested_at": datetime(2026, 10, 15, 12, 0, 0, 123456),
        **overrides,
    }


@pytest.fixture
def database(mocker):
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    mocker.patch.object(requests, "connect", return_value=connection)
    return connection, cursor


def test_button_is_timestamp_bound_and_payload_has_no_pings():
    payload = requests.request_payload(
        {**player(), "name": "@everyone"}, "https://site/pokedex/"
    )
    assert payload["allowed_mentions"] == {"parse": []}
    for button in payload["components"][0]["components"]:
        assert len(button["custom_id"]) <= 100
        assert requests.parse_button(button["custom_id"])[1:] == ("123", STAMP)
    assert requests.parse_button("shiny-access:accept:123:20269915120000123456") is None


@pytest.mark.parametrize(
    "row", [None, player(shiny_access_requested_at="2026-10-15 13:00:00")]
)
def test_stale_request_has_no_writes(database, row):
    connection, cursor = database
    cursor.fetchone.return_value = row
    assert requests.decide_request("123", STAMP, True) is False
    assert cursor.execute.call_count == 1
    connection.commit.assert_not_called()
    connection.rollback.assert_called_once()
    connection.close.assert_called_once()


@pytest.mark.parametrize(
    "link,relink,queues",
    [
        (None, 0, True),
        ({"status": "failed"}, 0, True),
        ({"status": "pending"}, 0, False),
        ({"status": "linked"}, 0, False),
        ({"status": "linked"}, 1, True),
    ],
)
def test_approval_mirrors_site_queue_rules(database, link, relink, queues):
    connection, cursor = database
    cursor.fetchone.side_effect = [player(shiny_relink=relink), link]
    assert requests.decide_request("123", STAMP, True)
    sql = [call.args[0] for call in cursor.execute.call_args_list]
    update = next(statement for statement in sql if "collecting =" in statement)
    assert "FIND_IN_SET('shiny', collecting)" in update
    assert "shiny_access = 1, shiny_map = 1" in update
    assert "shiny_relink = 0" in update
    assert "shiny_settings_revision + 1" in update
    assert any("INSERT INTO" in statement for statement in sql) is queues
    if queues:
        queue = next(
            call
            for call in cursor.execute.call_args_list
            if "INSERT INTO" in call.args[0]
        )
        assert queue.args[1] == ("123", "trainer", "123456789012", 0, 1)
        assert "link_processing_at = NULL" in queue.args[0]
    connection.commit.assert_called_once()
    connection.rollback.assert_not_called()
    connection.close.assert_called_once()


def test_rejection_does_not_queue_or_clear_relink(database):
    connection, cursor = database
    cursor.fetchone.return_value = player(shiny_relink=1)
    assert requests.decide_request("123", STAMP, False)
    assert cursor.execute.call_count == 2
    sql = cursor.execute.call_args.args[0]
    assert "shiny_access = 0, shiny_map = 0" in sql
    assert "shiny_access_requested_at = NULL" in sql
    assert "shiny_relink" not in sql
    connection.commit.assert_called_once()


def test_invalid_friend_code_rolls_back(database):
    connection, cursor = database
    cursor.fetchone.side_effect = [player(friend_code="invalid"), None]
    with pytest.raises(ValueError, match="Código"):
        requests.decide_request("123", STAMP, True)
    assert cursor.execute.call_count == 2
    connection.commit.assert_not_called()
    connection.rollback.assert_called_once()
    connection.close.assert_called_once()


def test_queue_failure_rolls_back_access(database):
    connection, cursor = database
    cursor.fetchone.side_effect = [player(), None]
    cursor.execute.side_effect = [None, None, None, None, RuntimeError("queue failed")]
    with pytest.raises(RuntimeError, match="queue failed"):
        requests.decide_request("123", STAMP, True)
    connection.rollback.assert_called_once()
    connection.commit.assert_not_called()
