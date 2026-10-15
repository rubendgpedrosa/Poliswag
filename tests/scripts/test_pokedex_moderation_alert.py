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
    assert retry.call_args.args[0]["embeds"][0]["url"].endswith("/jogador/456")
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
    mocker.patch.object(alerts, "member_enricher", return_value=lambda value: value)
    post = mocker.patch.object(alerts.dm_owner, "post")
    alerts.main()
    path, token, payload = post.call_args.args
    assert (path, token) == ("/channels/321/messages", "fake")
    assert payload["allowed_mentions"] == {"parse": []}
    assert len(payload["components"][0]["components"]) == 2
    post.assert_called_once()
    connection.close.assert_called_once()
    sql = connection.cursor.return_value.__enter__.return_value.execute.call_args.args[
        0
    ]
    assert "display_name, username, avatar_url" in sql


def test_enriches_only_unsent_requests(tmp_path):
    enrich = Mock(
        side_effect=lambda value: {
            **value,
            "display_name": "Guild Nick",
            "username": "discord.account",
        }
    )
    send = Mock()
    state = tmp_path / "state.json"
    alerts.notify_pending([row()], state, send, "https://site", enrich=enrich)
    alerts.notify_pending([row()], state, send, "https://site", enrich=enrich)
    enrich.assert_called_once()
    fields = send.call_args.args[0]["embeds"][0]["fields"]
    assert fields[0]["value"] == "Guild Nick"
    assert fields[1]["value"] == "@discord.account"


@pytest.mark.parametrize("avatar_kind", ["guild", "user", "default"])
def test_member_enrichment_uses_server_identity_and_avatar(mocker, avatar_kind):
    member = {
        "nick": "Guild Nick",
        "avatar": "guild_hash" if avatar_kind == "guild" else None,
        "user": {
            "username": "discord.account",
            "global_name": "Global Name",
            "avatar": "user_hash" if avatar_kind == "user" else None,
            "discriminator": "0",
        },
    }
    get = mocker.patch.object(
        alerts, "discord_get", side_effect=[{"guild_id": "789"}, member, member]
    )
    enrich = alerts.member_enricher(321, "fake")
    result = enrich(row())
    assert result["display_name"] == "Guild Nick"
    assert result["username"] == "discord.account"
    expected_path = {
        "guild": "/guilds/789/users/123/avatars/guild_hash.webp",
        "user": "/avatars/123/user_hash.webp",
        "default": "/embed/avatars/0.png",
    }[avatar_kind]
    assert expected_path in result["avatar_url"]
    enrich(row())
    assert [call.args[0] for call in get.call_args_list] == [
        "/channels/321",
        "/guilds/789/members/123",
        "/guilds/789/members/123",
    ]


def test_member_enrichment_falls_back_when_discord_is_unavailable(mocker):
    get = mocker.patch.object(alerts, "discord_get", side_effect=OSError("forbidden"))
    enrich = alerts.member_enricher(321, "fake")
    original = row()
    assert enrich(original) is original
    assert enrich(original) is original
    get.assert_called_once()


def test_member_without_nickname_uses_global_name(mocker):
    mocker.patch.object(
        alerts,
        "discord_get",
        side_effect=[
            {"guild_id": "789"},
            {"user": {"username": "discord.account", "global_name": "Global Name"}},
        ],
    )
    assert alerts.member_enricher(321, "fake")(row())["display_name"] == "Global Name"
