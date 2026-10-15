"""Discord delivery and durable per-request deduplication without live services."""

import json
from unittest.mock import Mock

import pytest

from scripts import pokedex_validation_alert as alerts


def row(owner="1", requested="2026-10-15 12:00:00"):
    return {
        "discord_id": owner,
        "name": "Trainer",
        "shiny_access_requested_at": requested,
    }


def test_requests_notify_once_and_new_requests_notify_again(tmp_path):
    state = tmp_path / "state.json"
    send = Mock()
    rows = [row()]
    assert (
        alerts.notify_pending(rows, state, send, "https://example.test/pokedex/") == 1
    )
    assert alerts.notify_pending(rows, state, send, "https://example.test/pokedex") == 0
    send.assert_called_once()
    assert send.call_args.args[0]["message"].endswith("/pokedex/procurar")
    assert alerts.notify_pending([], state, send, "https://example.test") == 0
    assert alerts.notify_pending(rows, state, send, "https://example.test") == 1
    rows = [row(requested="2026-10-15 13:00:00")]
    assert alerts.notify_pending(rows, state, send, "https://example.test") == 1


@pytest.mark.parametrize("existing", [False, True])
def test_failed_delivery_preserves_watermark_for_retry(tmp_path, existing):
    state = tmp_path / "state.json"
    if existing:
        state.write_text(json.dumps([["previous", "request"]]))
    before = state.read_bytes() if existing else None
    with pytest.raises(OSError, match="failed"):
        alerts.notify_pending(
            [row()], state, Mock(side_effect=OSError("failed")), "https://example.test"
        )
    assert (state.read_bytes() if state.exists() else None) == before
    send = Mock()
    assert alerts.notify_pending([row()], state, send, "https://example.test") == 1


@pytest.fixture
def delivery(mocker, tmp_path):
    from modules.config import Config

    mocker.patch.object(Config, "IS_PRODUCTION", True)
    mocker.patch.object(Config, "DISCORD_API_KEY", "test-token")
    mocker.patch.object(Config, "MY_ID", 123)
    mocker.patch.object(alerts, "STATE", tmp_path / "state.json")
    connection = mocker.patch("modules.database_connector.connect").return_value
    connection.cursor.return_value.__enter__.return_value.fetchall.return_value = [
        row()
    ]
    post = mocker.patch.object(
        alerts.dm_owner, "post", return_value={"id": "dm-channel"}
    )
    http = mocker.patch(
        "urllib.request.urlopen", side_effect=AssertionError("No real HTTP")
    )
    return post, http, connection


def test_main_sends_discord_embed_and_deduplicates(delivery):
    post, http, connection = delivery
    alerts.main()
    assert post.call_args_list[0].args == (
        "/users/@me/channels",
        "test-token",
        {"recipient_id": "123"},
    )
    path, token, payload = post.call_args_list[1].args
    assert (path, token) == ("/channels/dm-channel/messages", "test-token")
    assert payload["embeds"][0]["title"] == "Pokédex: validação pendente"
    assert "Trainer" in payload["embeds"][0]["description"]
    assert alerts.STATE.exists()
    alerts.main()
    assert post.call_count == 2
    http.assert_not_called()
    assert connection.close.call_count == 2


@pytest.mark.parametrize("failed_call", [1, 2])
def test_main_failed_discord_delivery_does_not_mark_as_sent(delivery, failed_call):
    post, http, _ = delivery
    post.side_effect = (
        [OSError("failed")]
        if failed_call == 1
        else [{"id": "dm-channel"}, OSError("failed")]
    )
    with pytest.raises(OSError, match="failed"):
        alerts.main()
    assert not alerts.STATE.exists()
    http.assert_not_called()
