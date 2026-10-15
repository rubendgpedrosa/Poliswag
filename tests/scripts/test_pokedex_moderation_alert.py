import json
from unittest.mock import Mock

import pytest

from scripts import pokedex_moderation_alert as alerts


def row(discord_id="123", requested="2026-10-15 12:00:00.123456"):
    return {
        "discord_id": discord_id,
        "name": "Trainer",
        "shiny_access_requested_at": requested,
    }


def test_partial_failure_persists_each_success_and_retries_only_unsent(tmp_path):
    state = tmp_path / "state.json"
    rows = [row(), row("456")]
    send = Mock(side_effect=[None, OSError("failed")])
    with pytest.raises(OSError):
        alerts.notify_pending(rows, state, send, "https://site/pokedex")
    assert json.loads(state.read_text()) == [["123", "20261015120000123456"]]
    retry = Mock()
    assert alerts.notify_pending(rows, state, retry, "https://site/pokedex") == 1
    assert "456" in retry.call_args.args[0]["embeds"][0]["description"]
    assert alerts.notify_pending(rows, state, retry, "https://site/pokedex") == 0


def test_resolved_and_new_timestamp_requests_notify_again(tmp_path):
    state = tmp_path / "state.json"
    send = Mock()
    assert alerts.notify_pending([row()], state, send, "https://site") == 1
    assert alerts.notify_pending([], state, send, "https://site") == 0
    assert alerts.notify_pending([row()], state, send, "https://site") == 1
    assert (
        alerts.notify_pending(
            [row(requested="2026-10-15 12:00:00.123457")], state, send, "https://site"
        )
        == 1
    )


def test_main_posts_directly_to_moderation_channel(mocker, tmp_path):
    from modules.config import Config

    mocker.patch.object(Config, "IS_PRODUCTION", True)
    mocker.patch.object(Config, "MOD_CHANNEL_ID", 321)
    mocker.patch.object(Config, "DISCORD_API_KEY", "fake")
    mocker.patch.object(alerts, "STATE", tmp_path / "state.json")
    connection = mocker.patch("modules.database_connector.connect").return_value
    connection.cursor.return_value.__enter__.return_value.fetchall.return_value = [
        row()
    ]
    post = mocker.patch.object(alerts.dm_owner, "post")
    alerts.main()
    path, token, payload = post.call_args.args
    assert (path, token) == ("/channels/321/messages", "fake")
    assert payload["allowed_mentions"] == {"parse": []}
    assert len(payload["components"][0]["components"]) == 2
    post.assert_called_once()
    connection.close.assert_called_once()
